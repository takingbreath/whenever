"""Writes one EZVIZ camera channel as plain video on stdout.

Most cameras send MPEG-PS, which `pyezvizapi stream dump` handles. Some, such as the H9c, send RTP,
which it cannot convert. This runs in the .ezviz Python, because it needs that library:
    .ezviz/bin/python ezviz_rtp.py BD1234567 1 | ffmpeg -f hevc -i pipe:0 ...
"""

import json
import sys

from pyezvizapi import EzvizClient
from pyezvizapi.__main__ import _detect_rtp_video_codec, _rtp_packets_to_annexb
from pyezvizapi.cloud_stream import open_cloud_stream

VIDEO_PAYLOAD_TYPE = 96


def is_video(packet):
    return len(packet.body) > 1 and packet.body[1] & 0x7F == VIDEO_PAYLOAD_TYPE


def is_frame_end(packet):
    return bool(packet.body[1] & 0x80)


def main():
    serial, channel = sys.argv[1], int(sys.argv[2])
    client = EzvizClient(token=json.loads(open("ezviz_token.json").read()))
    client.login()
    out = sys.stdout.buffer
    with open_cloud_stream(client, serial, channel=channel) as stream:
        stream.start()
        waiting, codec = [], None
        for packet in stream.iter_packets():
            if not is_video(packet):
                continue
            waiting.append(packet)
            if not is_frame_end(packet):
                continue
            codec = codec or _detect_rtp_video_codec(waiting)
            out.write(_rtp_packets_to_annexb(waiting, codec=codec))
            out.flush()
            waiting = []


if __name__ == "__main__":
    main()
