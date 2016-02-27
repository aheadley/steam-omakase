#!/usr/bin/env python2

import requests

STEAM_STOREFRONT_APP_DETAILS_URL = 'http://store.steampowered.com/api/appdetails/'

def get_app_info(app_id):
    resp = requests.get(STEAM_STOREFRONT_APP_DETAILS_URL,
        params={ 'appids': app_id })
    try:
        return resp.json()[str(app_id)]['data']
    except Exception as err:
        print app_id, repr(err)
        print 'https://steamdb.info/app/{}/'.format(app_id)
        print resp.content
        return None

def games_monad(steam_user):
    try:
        return steam_user.games
    except AttributeError as err:
        return []
