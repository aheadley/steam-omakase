#!/usr/bin/env python2

STEAM_STOREFRONT_APP_DETAILS_URL = 'http://store.steampowered.com/api/appdetails/'

def get_app_info(app_ids):
    try:
        app_ids_param = ','.join(app_ids)
    except TypeError as err:
        # not a list, cast to str
        app.logger.exception(err)
        app_ids_param = str(app_ids)

    resp = requests.get(STEAM_STOREFRONT_APP_DETAILS_URL,
        params={ 'appids': app_ids_param })

    resp_data = resp.json
    return resp_data

def games_monad(steam_user):
    try:
        return steam_user.games
    except AttributeError as err:
        return []
