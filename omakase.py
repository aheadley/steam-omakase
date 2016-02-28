#!/usr/bin/env python2

import os
import random
import re

import flask
import flask_bootstrap
import steamapi
import requests
import werkzeug.contrib.cache
import bmemcached

class OmakaseHelper(object):
    STEAMCOMMUNITY_URL_RE = re.compile(r'https?://(?:www\.)?steamcommunity.com/id/([^/]+)/.*')
    STOREFRONT_API_ENDPOINT = 'http://store.steampowered.com/api/{method}/'

    PLATFORMS = [
        'windows',
        'mac',
        'linux',
    ]

    def __init__(self, app):
        # http://steamcommunity.com/dev/apikey
        self._api_key = os.environ.get('STEAM_API_KEY')
        self._memcached_config = {
            'servers': os.environ.get('MEMCACHEDCLOUD_SERVERS').split(','),
            'username': os.environ.get('MEMCACHEDCLOUD_USERNAME'),
            'password': os.environ.get('MEMCACHEDCLOUD_PASSWORD'),
        }

        self._app = app
        self._api = steamapi.core.APIConnection(api_key=self._api_key)
        self._cache = werkzeug.contrib.cache.MemcachedCache(
            bmemcached.Client(self._memcached_config['servers'],
                self._memcached_config['username'],
                self._memcached_config['password']))

    def fetch_user_by_id(self, user_id):
        v = self._cache.get(self._cache_key('user', user_id))
        if v is not None:
            return v
        try:
            user = steamapi.user.SteamUser(userid=user_id)
        except Exception as err:
            self._app.logger.exception(err)
            return None
        self._cache.set(self._cache_key('user', user.id), user, timeout=3600)
        return user

    def fetch_user_by_url_token(self, url_token):
        return steamapi.user.SteamUser(userurl=url_token)

    def fetch_friends_by_user(self, user):
        if self.user_is_public(user):
            return user.friends
        else:
            return []

    def fetch_games_by_user(self, user):
        if self.user_is_public(user):
            return user.games
        else:
            return []

    def user_is_public(self, user):
        return user.privacy >= 3

    def fetch_appdetails_by_id(self, app_id):
        v = self._cache.get(self._cache_key('app', app_id))
        if v is not None:
            return v
        app_data = self._storefront_request('appdetails', appids=app_id)
        if app_data is not None and app_data[str(app_id)]['success']:
            app_data = app_data[str(app_id)]['data']
        else:
            return None
        self._cache.set(self._cache_key('app', app_id), app_data, timeout=3600 * 24 * 7)
        return app_data

    def get_game_intersection(self, steam_user, friends, platforms):
        game_ids = set(g.id for g in helper.fetch_games_by_user(steam_user))

        for friend in friends:
            if friend.privacy >= 3:
                game_ids &= set(g.id for g in helper.fetch_games_by_user(friend))

        game_info = [helper.fetch_appdetails_by_id(gid) for gid in game_ids]
        game_info = [g for g in game_info if g is not None]
        game_info = [g for g in game_info \
            if 1 in [c['id'] for c in g['categories']] \
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
        resp = requests.get(req_url, params=kwargs)
        return resp.json()

app = flask.Flask(__name__)
flask_bootstrap.Bootstrap(app)

helper = OmakaseHelper(app)

@app.route('/')
def index():
    return flask.render_template('index.html')

@app.route('/about')
def about():
    return flask.render_template('about.html')

@app.route('/user/search', methods=['POST'])
def select_user():
    query_string = flask.request.form['query_string']
    if query_string.isdigit():
        steam_user = helper.fetch_user_by_id(int(query_string))
    else:
        match = self.STEAMCOMMUNITY_URL_RE.match(query_string)
        if match:
            query_string = m.group(1)
        try:
            steam_user = helper.fetch_user_by_url_token(query_string)
        except steamapi.user.UserNotFoundError as err:
            return flask.redirect(flask.url_for('index',
                msg='Couldn\'t find a user with that url'))
    if not helper.user_is_public(steam_user):
        return flask.redirect(flask.url_for('index',
            msg='It looks like your profile isn\'t public, {}'.format(steam_user.name)))
    return flask.redirect(flask.url_for('select_friends',
        user_id=steam_user.id))

@app.route('/user/<int:user_id>/friends/')
def select_friends(user_id):
    steam_user = helper.fetch_user_by_id(user_id)

    return flask.render_template('select_friends.html',
        steam_user=steam_user, steam_friends=helper.fetch_friends_by_user(steam_user))

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

    shared_games = helper.get_game_intersection(steam_user, friends, platforms)

    if 'omakase' in flask.request.form and flask.request.form['omakase'] == 'true':
        selected_game = helper.choose_game(steam_user, friends, shared_games)
        return flask.render_template('game_intersection_omakase.html',
            steam_user=steam_user,
            steam_friends=friends,
            the_game=selected_game)
    else:
        return flask.render_template('game_intersection_list.html',
            steam_user=steam_user,
            steam_friends=friends,
            shared_games=shared_games)

if __name__ == '__main__':
    app.run(debug=True)
