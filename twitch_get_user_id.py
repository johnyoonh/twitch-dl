#!/usr/bin/env python3
import os
import sys

from twitch.constants import Twitch
from util.contents import Contents


def main():
    try:
        user_name = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.readline().strip()
        user_id = user_id_of(user_name)
        sys.stdout.write(user_id + os.linesep)
    except ValueError as error:
        sys.stderr.write(str(error) + os.linesep)
        exit(1)


def user_id_of(channel_name):
    data = user_data_for(channel_name)['data']
    print data
    if len(data) == 0:
        raise ValueError('User could not be found!')
    return data[0]['id']


def user_data_for(channel_id, isID=False):
    if isID:
        params = {'id': channel_id}
    else:
        params = {'login': channel_id}
    return Contents.json(
        'https://api.twitch.tv/helix/users',
        params=params,
        headers=Twitch.client_id_header,
        onerror=lambda _: raise_error('Failed to get the users list!')
    )['data'][0]


def raise_error(message):
    raise ValueError(message)


if __name__ == '__main__':
    main()
