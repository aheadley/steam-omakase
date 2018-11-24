#!/usr/bin/env python2

import os
import random
import re
import logging
import pprint

import flask
import flask_bootstrap
import flask.ext.compress
import steamapi
import requests
import werkzeug.contrib.cache
import bmemcached
from requests_futures.sessions import FuturesSession

DEBUG_MODE = bool(os.environ.get('OMAKASE_DEBUG'))
NEGATIVE_CACHE_HIT = -1

class OmakaseHelper(object):
    STEAMCOMMUNITY_URL_RE = re.compile(r'(?:https?://)?(?:www\.)?steamcommunity.com/(?:id|profiles?)/([^/#?]+)', re.I)
    STOREFRONT_API_ENDPOINT = 'https://store.steampowered.com/api/{method}/'

    PLATFORMS = [
        'windows',
        'mac',
        'linux',
    ]

    MULTIPLAYER_CATEGORIES = [
        1,  # "Multi-player"
        9,  # "Co-op"
        # left out because it doesn't matter whether your friends have a copy
        # 24, # "Local Co-op"
        27, # "Cross-Platform Multiplayer"
    ]

    def __init__(self, app):
        # http://steamcommunity.com/dev/apikey
        self._api_key = os.environ.get('STEAM_API_KEY')

        if os.environ.get('RUNNING_IN_HEROKU', False):
            self._memcached_config = {
                'servers': os.environ.get('MEMCACHEDCLOUD_SERVERS'),
                'username': os.environ.get('MEMCACHEDCLOUD_USERNAME'),
                'password': os.environ.get('MEMCACHEDCLOUD_PASSWORD'),
            }

            self._cache = werkzeug.contrib.cache.MemcachedCache(
                bmemcached.Client(self._memcached_config['servers'].split(','),
                    self._memcached_config['username'],
                    self._memcached_config['password']))
        else:
            self._memcached_config = {
                'servers': os.environ.get('MEMCACHED_SERVERS', ''),
            }

            self._cache = werkzeug.contrib.cache.MemcachedCache(
                bmemcached.Client(os.environ.get('MEMCACHED_SERVERS').split(',')))

        self._app = app
        self._api = steamapi.core.APIConnection(api_key=self._api_key)

    def fetch_user_by_id(self, user_id, use_cache=True):
        cache_key = self._cache_key('user', user_id)
        if use_cache:
            user = self._cache.get(cache_key)
        else:
            user = None
        if user is None:
            self._app.logger.debug('User cache MISS: %s', cache_key)
            try:
                user = steamapi.user.SteamUser(userid=user_id)
            except Exception as err:
                self._app.logger.exception(err)
                return None
            self._cache.set(cache_key, user, timeout=3600 * 24 * 1)
        else:
            self._app.logger.debug('User cache HIT: %s:%s', cache_key, user.name)

        return user

    def fetch_user_by_url_token(self, url_token):
        return steamapi.user.SteamUser(userurl=url_token)

    def fetch_friends_by_user(self, user):
        if self.user_is_public(user):
            self._app.logger.debug('Getting user friends...')
            cache_key = self._cache_key('user-friends', user.id)
            friend_ids = self._cache.get(cache_key)
            if friend_ids is None:
                self._app.logger.debug('User friends cache MISS: %s', cache_key)
                friend_ids = [friend.id for friend in user.friends]
                self._cache.set(cache_key, friend_ids, timeout=3600 * 24 * 3)
            else:
                self._app.logger.debug('User friends cache HIT: %s', cache_key)
        else:
            friend_ids = []

        friends = self._fetch_many_by_id(friend_ids, 'user', self.fetch_user_by_id)
        return friends

    def _fetch_many_by_id(self, obj_ids, key_ns, fetch_method):
        self._app.logger.debug('Pulling object IDs from cache: %s:%d', key_ns, len(obj_ids))
        cache_keys = [self._cache_key(key_ns, obj_id) for obj_id in obj_ids]
        cached_objs = zip(obj_ids, self._cache.get_many(*cache_keys))
        cache_hits = len(list(obj_id for obj_id, obj in cached_objs if obj))
        self._app.logger.debug('%s multicache HIT/MISS: %d/%d', key_ns, cache_hits, len(cached_objs))
        self._app.logger.debug('Filling in %s cache misses...', key_ns)
        objs = [obj if obj else fetch_method(obj_id, use_cache=False) \
            for obj_id, obj in cached_objs]

        return objs

    def fetch_games_by_user(self, user):
        if self.user_is_public(user):
            self._app.logger.debug('Getting user games...')
            cache_key = self._cache_key('user-games', user.id)
            game_ids = self._cache.get(cache_key)
            if game_ids is None:
                self._app.logger.debug('User games cache MISS: %s', cache_key)
                game_ids = [game.id for game in user.games]
                self._cache.set(cache_key, game_ids, timeout=3600 * 24 * 3)
            else:
                self._app.logger.debug('User games cache HIT: %s', cache_key)
        else:
            game_ids = []

        game_cache_keys = [self._cache_key('app', game_id) for game_id in game_ids]
        games = dict(zip(game_ids, self._cache.get_many(*game_cache_keys)))
        all_uncached_game_ids = [game_id for game_id, game in games.iteritems() if game is None]
        with FuturesSession(max_workers=2) as session:
            chunk_size = 20
            self._app.logger.debug('Working on items: %s', len(all_uncached_game_ids))
            for i in range(0, len(all_uncached_game_ids), chunk_size):
                self._app.logger.debug('Working on items in chunk: %d<>%d', i, i+chunk_size)
                uncached_game_ids = all_uncached_game_ids[i:i+chunk_size]
                req_url = self.STOREFRONT_API_ENDPOINT.format(method='appdetails')
                do_req = lambda app_id: session.get(req_url, timeout=5.0, params={'appids': app_id}, headers={'User-Agent': flask.request.headers['User-Agent']})
                game_requests = [do_req(game_id) for game_id in uncached_game_ids]

                uncached_games = dict(zip(uncached_game_ids, game_requests))
                for game_id, game_request in uncached_games.iteritems():
                    try:
                        game_response = game_request.result()
                        if game_response.ok:
                            game_result = game_response.json()
                        else:
                            game_result = None
                    except Exception as err:
                        self._app.logger.exception(err)
                        self._app.logger.debug('r.text=%s r.headers=%r', game_response.text, game_response.headers)
                        game_result = None
                    if game_result is not None and game_result[str(game_id)]['success']:
                        uncached_games[game_id] = game_result[str(game_id)]['data']
                    else:
                        uncached_games[game_id] = NEGATIVE_CACHE_HIT
                    self._app.logger.debug('%s=%s', game_id, bool(game_result))
                cache_update = [(self._cache_key('app', uncached_game_id), uncached_games[uncached_game_id]) for uncached_game_id in uncached_game_ids]
                self._cache.set_many(cache_update, timeout=3600 * 3)
                games.update({k: v for k, v in uncached_games.iteritems() if v})

        return games

    def user_is_public(self, user):
        return user.privacy >= 3

    def fetch_appdetails_by_id(self, app_id, use_cache=True):
        cache_key = self._cache_key('app', app_id)
        if use_cache:
            app_data = self._cache.get(cache_key)
        else:
            app_data = None

        if app_data is None or app_data == NEGATIVE_CACHE_HIT:
            if app_data is NEGATIVE_CACHE_HIT:
                self._app.logger.debug('App cache NEGATIVE HIT: %s', cache_key)
                return None

            self._app.logger.debug('App cache MISS: %s', cache_key)
            sf_response = self._storefront_request('appdetails', appids=app_id)

            if sf_response is not None and sf_response[str(app_id)]['success']:
                app_data = sf_response[str(app_id)]['data']
            else:
                self._app.logger.debug('Failed to pull app details from storefront: %s', app_id)
                self._app.logger.debug('sf_response=%s', sf_response)
                self._cache.set(cache_key, NEGATIVE_CACHE_HIT, timeout=3600 * 3)
                return None

            self._cache.set(cache_key, app_data, timeout=3600 * 24 * 30)
        else:
            self._app.logger.debug('App cache HIT: %s:%s', cache_key, app_data['name'])
        return app_data

    def get_game_intersection(self, steam_user, friends, platforms):
        game_ids = set(g['steam_appid'] for g in self.fetch_games_by_user(steam_user) if isinstance(g, dict))

        for friend in friends:
            if self.user_is_public(friend):
                game_ids &= set(g['steam_appid'] for g in self.fetch_games_by_user(friend) if isinstance(g, dict))

        # game_info = [self.fetch_appdetails_by_id(gid) for gid in game_ids]
        game_info = self._fetch_many_by_id(game_ids, 'app', self.fetch_appdetails_by_id)
        game_info = [g for g in game_info if g is not None]
        game_info = [g for g in game_info \
            if any([c['id'] in self.MULTIPLAYER_CATEGORIES for c in g.get('categories', [])]) \
                and g['type'] == 'game'
                and all(g['platforms'][v] for v in platforms)]

        return game_info

    def choose_game(self, steam_user, steam_friends, shared_games):
        return random.choice(shared_games)

    def normalize_friend_ids(self, friend_ids):
        friends = map(self.fetch_user_by_id,
            list(set(int(friend_id) for friend_id in friend_ids \
                if friend_id.isdigit())))
        return friends

    def normalize_platforms(self, os_list):
        return [os for os in os_list if os in self.PLATFORMS]

    def _cache_key(self, namespace, id):
        return 'steam_{}_{:016d}'.format(namespace, id)

    def _storefront_request(self, method, **kwargs):
        req_url = self.STOREFRONT_API_ENDPOINT.format(method=method)
        resp = requests.get(req_url, params=kwargs, timeout=5.0)
        return resp.json()

app = flask.Flask(__name__)
flask_bootstrap.Bootstrap(app)
flask.ext.compress.Compress(app)

app.config['COMPRESS_LEVEL'] = 1

helper = OmakaseHelper(app)

@app.before_first_request
def setup_logging():
    app.logger.addHandler(logging.StreamHandler())
    if DEBUG_MODE:
        app.logger.setLevel(logging.DEBUG)
    else:
        app.logger.setLevel(logging.INFO)

    app.logger.info('Running in DEBUG: %s', DEBUG_MODE)

@app.route('/')
def index():
    return flask.render_template('index.html')

@app.route('/about')
def about():
    return flask.render_template('about.html')

@app.route('/user/search', methods=['POST'])
def select_user():
    query_string = flask.request.form['query_string']
    if not query_string:
        return flask.redirect(flask.url_for('index',
            msg='Gotta give me something to search for'))
    app.logger.info('Searching for: "%s"', query_string)

    match = helper.STEAMCOMMUNITY_URL_RE.match(query_string)
    if match:
        query_string = match.group(1)

    if query_string.isdigit():
        steam_user = helper.fetch_user_by_id(int(query_string))
    else:
        try:
            steam_user = helper.fetch_user_by_url_token(query_string)
        except (steamapi.user.UserNotFoundError, ValueError) as err:
            return flask.redirect(flask.url_for('index',
                msg='Couldn\'t find a user with that vanity url or ID'))

    app.logger.info('Selected user: %s (%s)', steam_user.name, steam_user.id)

    if not helper.user_is_public(steam_user):
        return flask.redirect(flask.url_for('index',
            msg='It looks like your profile isn\'t public, {}'.format(steam_user.name)))

    return flask.redirect(flask.url_for('select_friends',
        user_id=steam_user.id))

@app.route('/user/<int:user_id>/friends/')
def select_friends(user_id):
    steam_user = helper.fetch_user_by_id(user_id)
    steam_friends = helper.fetch_friends_by_user(steam_user)

    app.logger.info('Fetched %d friends for user: %s',
        len(steam_friends), steam_user.name)

    return flask.render_template('select_friends.html',
        steam_user=steam_user, steam_friends=steam_friends)

@app.route('/user/<int:user_id>/game/', methods=['POST'])
def game_intersection(user_id):
    steam_user = helper.fetch_user_by_id(user_id)
    friends = helper.normalize_friend_ids(flask.request.form.getlist('friend_ids'))
    platforms = helper.normalize_platforms(flask.request.form.getlist('os'))

    if len(friends) == 0:
        return flask.redirect(flask.url_for('select_friends',
            user_id=user_id,
            msg='Looks like you forgot to pick some friends'))
    if len(platforms) == 0:
        return flask.redirect(flask.url_for('select_friends',
            user_id=user_id,
            msg='I need to know what platforms to check support for'))

    app.logger.info('Intersecting %s and %s on platforms: %s',
        steam_user.name, ', '.join(f.name for f in friends),
        ', '.join(platforms))

    shared_games = helper.get_game_intersection(steam_user, friends, platforms)

    if 'omakase' in flask.request.form \
            and flask.request.form['omakase'] == 'true' \
            and len(shared_games) > 0:
        selected_game = helper.choose_game(steam_user, friends, shared_games)

        app.logger.info('Omakase choice: [appid:%s] %s',
            selected_game['steam_appid'], selected_game['name'])

        return flask.render_template('game_intersection_omakase.html',
            steam_user=steam_user,
            steam_friends=friends,
            the_game=selected_game)
    else:
        return flask.render_template('game_intersection_list.html',
            steam_user=steam_user,
            steam_friends=friends,
            shared_games=shared_games)

if DEBUG_MODE:
    @app.route('/user/<int:user_id>/game/<int:app_id>')
    def test_omakase_template(user_id, app_id):
        return flask.render_template('game_intersection_omakase.html',
            steam_user=helper.fetch_user_by_id(user_id),
            steam_friends=[],
            the_game=helper.fetch_appdetails_by_id(app_id))

    @app.route('/debug/cache', methods=['GET', 'DELETE'])
    def flush_cache():
        helper._cache._client.flush_all()
        return 'OK'

    @app.route('/debug/cache/<cache_key>', methods=['DELETE'])
    def flush_cache_key(cache_key):
        helper._cache._client.delete(cache_key)
        return 'OK'

    @app.route('/debug/cache-stats')
    def cache_stats():
        stats = helper._cache._client.stats()
        return pprint.pformat(stats)

    @app.route('/debug')
    def debug_dump():
        return 'request.headers={}'.format(flask.request.headers['User-Agent'])


if __name__ == '__main__':
    app.run(debug=DEBUG_MODE)
