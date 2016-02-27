#!/usr/bin/env python2

import os

import flask
import flask_bootstrap
import steamapi
import requests
import werkzeug.contrib.cache

class OmakaseHelper(object):
    STOREFRONT_API_ENDPOINT = 'http://store.steampowered.com/api/{method}/'

    def __init__(self, app):
        # http://steamcommunity.com/dev/apikey
        self._api_key = os.environ.get('STEAM_API_KEY')
        self._memcached_servers = os.environ.get('MEMCACHED_SERVERS')

        self._app = app
        self._api = steamapi.core.APIConnection(api_key=self._api_key)
        self._cache = werkzeug.contrib.cache.MemcachedCache(self._memcached_servers)

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

    def get_game_intersection(self, steam_user, friends):
        game_ids = set(g.id for g in helper.fetch_games_by_user(steam_user))

        for friend in friends:
            if friend.privacy >= 3:
                game_ids &= set(g.id for g in helper.fetch_games_by_user(friend))

        game_info = [helper.fetch_appdetails_by_id(gid) for gid in game_ids]
        game_info = [g for g in game_info if g is not None]
        game_info = [g for g in game_info \
            if 1 in [c['id'] for c in g['categories']] \
                and g['type'] == 'game']

        return game_info

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

@app.route('/user/search/<query_string>', methods='POST')
def select_user(query_string):
    if query_string.isdigit():
        steam_user = helper.fetch_user_by_id(int(query_string))
    else:
        steam_user = helper.fetch_user_by_url_token(query_string)
    return flask.redirect(flask.url_for('select_friends',
        user_id=steam_user.id))

@app.route('/user/<int:user_id>/friends/')
def select_friends(user_id):
    steam_user = helper.fetch_user_by_id(user_id)

    return flask.render_template('select_friends.html',
        steam_user=steam_user, steam_friends=[f for f in helper.fetch_friends_by_user(steam_user) if f.privacy >= 3])

@app.route('/games/<int:user_id>/<friend_ids>/')
def game_intersection_list(user_id, friend_ids):
    steam_user = helper.fetch_user_by_id(user_id)
    friend_ids = list(set(int(fid) for fid in friend_ids.split(',')))
    friends = [helper.fetch_user_by_id(fid) for fid in friend_ids]

    shared_games = helper.get_game_intersection(steam_user, friends)

    return flask.render_template('game_intersection_list.html',
        steam_user=steam_user,
        steam_friends=friends,
        shared_games=shared_games)

@app.route('/games/<int:user_id>/<friend_ids>/omakase')
def game_omakase(user_id, friend_ids):
    steam_user = helper.fetch_user_by_id(user_id)
    friend_ids = list(set(int(fid) for fid in friend_ids.split(',')))
    friends = [helper.fetch_user_by_id(fid) for fid in friend_ids]

    shared_games = helper.get_game_intersection(steam_user, friends)

    return 'NYI'

if __name__ == '__main__':
    app.run(debug=True)
