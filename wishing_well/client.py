import json
import logging
from json.decoder import JSONDecodeError
from time import sleep
from urllib.error import URLError, HTTPError
from urllib.request import urlopen
from urllib.parse import urlparse, urlencode, parse_qs

from .enums import ItemType
from .exceptions import AuthTokenExtractionError, MissingAuthTokenError, EndpointError, RequestError
from .database import Database


class Client:
    API_BASE_URL = 'https://hk4e-api-os.mihoyo.com/event/gacha_info/api/'

    def __init__(self):
        self._region = None
        self._auth_token = None
        self._database = Database()

    def _request(self, endpoint, extra_params=None):
        if self._region is None or self._auth_token is None:
            raise MissingAuthTokenError('Missing auth token.')

        params = {
            'lang': 'en',
            'authkey': self._auth_token,
            'authkey_ver': 1
        }
        if extra_params is not None:
            params = params | extra_params

        logging.info('Requesting endpoint %s', endpoint)
        try:
            with urlopen('{}{}?{}'.format(self.API_BASE_URL, endpoint, urlencode(params))) as request:
                result = request.read()
        except (URLError, HTTPError) as err:
            logging.error(err)
            raise EndpointError('Error making request.')

        try:
            result = json.loads(result)
        except JSONDecodeError as err:
            logging.error(err)
            raise RequestError('Error parsing request result as JSON.')

        if 'retcode' not in result:
            logging.error('Response had no "retcode" field')
            raise EndpointError('Malformed response from endpoint.')

        if result['retcode'] != 0:
            pretty_message = result['message'][0].upper() + result['message'][1:] + '.'
            logging.error(pretty_message)
            raise EndpointError(pretty_message)

        return result['data']

    def _fetch_wish_history(self, banner_type, end_id=None):
        params = {
            'gacha_type': banner_type,
            'size': 20
        }

        if end_id is not None:
            params['end_id'] = end_id

        latest_wish = None
        while (result := self._request('getGachaLog', params)) and len(result['list']) > 0:
            end_id = None
            for wish in result['list']:
                # get the latest wish and store it;
                # this is the earliest point we can do this, because
                # only when we start fetching wish history from
                # mihoyo's API will we get the UID for our auth token
                if latest_wish is None:
                    latest_wish = self._database.get_latest_wish(wish['uid'], banner_type)

                # return when we reach the latest wish we already have in our history
                if latest_wish is not None and latest_wish['id'] == int(wish['id']):
                    return

                yield wish
                end_id = wish['id']

            params['end_id'] = end_id
            sleep(0.1)  # reasonable delay..?

    def set_region_and_auth_token(self, region, auth_token):
        self._region = region
        self._auth_token = auth_token

    def fetch_and_store_banner_types(self):
        logging.info('Fetching banner types')
        banner_types = {}
        result = self._request('getConfigList')
        for banner_type in result['gacha_type_list']:
            banner_types[banner_type['key']] = banner_type['name']
        self._database.store_banner_types(banner_types)

    def fetch_and_store_wish_history(self):
        logging.info('Fetching wish history')
        new_wishes_count = 0
        for banner_type in self._database.get_banner_types().keys():
            logging.info('Fetching wish history for banner type %s', banner_type)
            uid = None
            wishes = []
            wish_history = self._fetch_wish_history(banner_type)
            for wish in wish_history:
                if uid is None:
                    uid = wish['uid']

                wishes.append({
                    'id': int(wish['id']),  # convert to int for proper sorting
                    'time': wish['time'],
                    'type': ItemType.CHARACTER if wish['item_type'] == 'Character' else ItemType.WEAPON,
                    'rarity': int(wish['rank_type']),
                    'name': wish['name'],
                })

            logging.info('Got %d wishes', len(wishes))
            new_wishes_count += len(wishes)
            wishes.sort(key=lambda wish: wish['id'])
            self._database.store_wish_history(uid, banner_type, wishes)

        return new_wishes_count

    def get_banner_types(self):
        return self._database.get_banner_types()

    def get_uids(self):
        return self._database.get_uids()

    def get_banner_types_for_uid(self, uid):
        return self._database.get_banner_types_for_uid(uid)

    def get_wish_history(self, uid, banner_type):
        return self._database.get_wish_history(uid, banner_type)

    @staticmethod
    def extract_region_and_auth_token(url):
        try:
            url = urlparse(url)
        except ValueError:
            raise AuthTokenExtractionError('Error parsing URL.')

        query_params = parse_qs(url.query)
        if 'authkey' not in query_params:
            raise AuthTokenExtractionError('Parameter "authkey" missing from URL.')
        if 'game_biz' not in query_params:
            raise AuthTokenExtractionError('Parameter "game_biz" missing from URL.')

        return (query_params['game_biz'][0], query_params['authkey'][0])