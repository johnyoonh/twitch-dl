from twitch_dl.twitch.constants import Twitch
from twitch_dl.util.contents import Contents


class Vod:
    @staticmethod
    def meta(vod_id):
        return Contents.json(
            'https://api.twitch.tv/kraken/videos/v{}'.format(vod_id),
            headers=Twitch.client_id_header
        )

def main():
    print Vod.meta("243609286")


if __name__ == '__main__':
    main()
