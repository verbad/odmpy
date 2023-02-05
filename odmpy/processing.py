# Copyright (C) 2023 github.com/ping
#
# This file is part of odmpy.
#
# odmpy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# odmpy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with odmpy.  If not, see <http://www.gnu.org/licenses/>.
#

import base64
import datetime
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree
from collections import OrderedDict

try:
    from functools import reduce
except ImportError:
    pass

import requests
from requests.adapters import HTTPAdapter, Retry
from requests.exceptions import HTTPError, ConnectionError
from termcolor import colored
from tqdm import tqdm
import eyed3

from .utils import unescape_html, slugify, mp3_duration_ms
from .shared import (
    generate_names,
    write_tags,
    generate_cover,
    merge_into_mp3,
    convert_to_m4b,
)
from .constants import OMC, OS, UA, UNSUPPORTED_PARSER_ENTITIES
from .libby import USER_AGENT, merge_toc


MARKER_TIMESTAMP_MMSS = r"(?P<min>[0-9]+):(?P<sec>[0-9]+)\.(?P<ms>[0-9]+)"
MARKER_TIMESTAMP_HHMMSS = (
    r"(?P<hr>[0-9]+):(?P<min>[0-9]+):(?P<sec>[0-9]+)\.(?P<ms>[0-9]+)"
)


def process_odm(odm_file, args, logger, cleanup_odm_license=False):
    """
    Download the audiobook loan using the specified odm file

    :param odm_file:
    :param args:
    :param logger:
    :param cleanup_odm_license:
    :return:
    """
    ffmpeg_loglevel = "info" if logger.level == logging.DEBUG else "error"
    xml_doc = xml.etree.ElementTree.parse(odm_file)
    root = xml_doc.getroot()
    metadata = None
    for t in root.itertext():
        if not t.startswith("<Metadata>"):
            continue
        # remove invalid '&' char
        text = re.sub(r"\s&\s", " &amp; ", t)
        try:
            metadata = xml.etree.ElementTree.fromstring(text)
        except xml.etree.ElementTree.ParseError:
            # [TODO]: Find a more generic solution instead of patching entities, maybe lxml?
            # Ref: https://github.com/ping/odmpy/issues/19
            patched_text = "<!DOCTYPE xml [{patch}]>{text}".format(
                patch="".join(
                    [
                        f'<!ENTITY {entity} "{replacement}">'
                        for entity, replacement in UNSUPPORTED_PARSER_ENTITIES.items()
                    ]
                ),
                text=text,
            )
            metadata = xml.etree.ElementTree.fromstring(patched_text)
        break

    debug_meta = {}

    title = metadata.find("Title").text
    cover_url = (
        metadata.find("CoverUrl").text if metadata.find("CoverUrl") is not None else ""
    )
    authors = [
        unescape_html(c.text)
        for c in metadata.find("Creators")
        if "Author" in c.attrib.get("role", "")
    ]
    if not authors:
        authors = [
            unescape_html(c.text)
            for c in metadata.find("Creators")
            if "Editor" in c.attrib.get("role", "")
        ]
    if not authors:
        authors = [unescape_html(c.text) for c in metadata.find("Creators")]
    narrators = [
        unescape_html(c.text)
        for c in metadata.find("Creators")
        if "Narrator" in c.attrib.get("role", "")
    ]
    publisher = metadata.find("Publisher").text
    description = (
        metadata.find("Description").text
        if metadata.find("Description") is not None
        else ""
    )

    debug_meta["meta"] = {
        "title": title,
        "coverUrl": cover_url,
        "authors": authors,
        "publisher": publisher,
        "description": description,
    }

    # View Book Info
    if args.command_name == "info":
        if args.format == "text":
            logger.info(f'{"Title:":10} {colored(title, "blue")}')
            logger.info(
                "{:10} {}".format(
                    "Creators:",
                    colored(
                        ", ".join(
                            [
                                f"{c.text} ({c.attrib['role']})"
                                for c in metadata.find("Creators")
                            ]
                        ),
                        "blue",
                    ),
                )
            )
            logger.info(f"{'Publisher:':10} {metadata.find('Publisher').text}")
            logger.info(
                f"{'Subjects:':10} {', '.join([c.text for c in metadata.find('Subjects')])}"
            )
            logger.info(
                f"{'Languages:':10} {', '.join([c.text for c in metadata.find('Languages')])}"
            )
            logger.info(f"{'Description:':10}\n{metadata.find('Description').text}")

            for formats in root.findall("Formats"):
                for f in formats:
                    logger.info(f"\n{'Format:':10} {f.attrib['name']}")
                    parts = f.find("Parts")
                    for p in parts:
                        logger.info(
                            f"* {p.attrib['name']} - {p.attrib['duration']} ({math.ceil(1.0 * int(p.attrib['filesize']) / 1024):,.0f}kB)"
                        )

        elif args.format == "json":
            result = {
                "title": title,
                "creators": [
                    f"{c.text} ({c.attrib['role']})" for c in metadata.find("Creators")
                ],
                "publisher": metadata.find("Publisher").text,
                "subjects": [c.text for c in metadata.find("Subjects")],
                "languages": [c.text for c in metadata.find("Languages")],
                "description": metadata.find("Description").text,
                "formats": [],
            }

            for formats in root.findall("Formats"):
                for f in formats:
                    parts = []
                    total_secs = 0
                    for p in f.find("Parts"):
                        part_duration = p.attrib["duration"]
                        # part duration can look like '%M:%S.%f' or '%H:%M:%S.%f'
                        try:
                            mins, secs = map(float, part_duration.split(":"))
                            hrs = 0
                        except ValueError:  # ValueError: too many values to unpack
                            hrs, mins, secs = map(float, part_duration.split(":"))
                        total_secs += hrs * 60 * 60 + secs + mins * 60
                        parts.append(
                            {
                                "name": p.attrib["name"],
                                "duration": part_duration,
                                "filesize": f"{math.ceil(1.0 * int(p.attrib['filesize']) / 1024):,.0f}kB",
                            }
                        )
                    result["formats"].append(
                        {"format": f.attrib["name"], "parts": parts}
                    )
                    # in case there are multiple formats, only need to store it once
                    if "total_duration" not in result:
                        result["total_duration"] = {
                            "total_minutes": round(total_secs / 60),
                            "total_seconds": round(total_secs),
                        }

            logger.info(json.dumps(result))

        sys.exit()

    session = requests.Session()
    custom_adapter = HTTPAdapter(
        max_retries=Retry(total=args.retries, backoff_factor=0.1)
    )
    for prefix in ("http://", "https://"):
        session.mount(prefix, custom_adapter)

    # Download Book
    download_baseurl = ""
    download_parts = []
    for formats in root.findall("Formats"):
        for f in formats:
            protocols = f.find("Protocols")
            for p in protocols:
                if p.attrib.get("method", "") != "download":
                    continue
                download_baseurl = p.attrib["baseurl"]
                break
            parts = f.find("Parts")
            for p in parts:
                download_parts.append(p.attrib)
    debug_meta["download_parts"] = download_parts

    logger.info(
        f'Downloading "{colored(title, "blue", attrs=["bold"])}" '
        f'by "{colored(", ".join(authors), "blue", attrs=["bold"])}" '
        f"in {len(download_parts)} part(s)..."
    )

    book_folder, book_filename, book_m4b_filename = generate_names(title, authors, args)

    # check early if a merged file is already saved
    if args.merge_output and os.path.isfile(
        book_filename if args.merge_format == "mp3" else book_m4b_filename
    ):
        logger.warning(
            'Already saved "%s"',
            colored(
                book_filename if args.merge_format == "mp3" else book_m4b_filename,
                "magenta",
            ),
        )
        if cleanup_odm_license and os.path.isfile(odm_file):
            try:
                os.remove(odm_file)
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f'Error deleting "{odm_file}": {str(e)}')
        sys.exit()

    debug_filename = os.path.join(book_folder, "debug.json")

    cover_filename, cover_bytes = generate_cover(
        book_folder, cover_url, session, args.timeout, logger
    )

    acquisition_url = root.find("License").find("AcquisitionUrl").text
    media_id = root.attrib["id"]

    client_id = str(uuid.uuid1()).upper()
    raw_hash = f"{client_id}|{OMC}|{OS}|ELOSNOC*AIDEM*EVIRDREVO"
    m = hashlib.sha1(raw_hash.encode("utf-16-le"))
    license_hash = base64.b64encode(m.digest())

    # Extract license
    # License file is downloadable only once per odm
    # so we keep it in case downloads fail
    _, odm_filename = os.path.split(odm_file)
    license_file = os.path.join(
        args.download_dir, odm_filename.replace(".odm", ".license")
    )

    if os.path.isfile(license_file):
        logger.warning(f"Already downloaded license file: {license_file}")
    else:
        # download license file
        params = OrderedDict(
            [
                ("MediaID", media_id),
                ("ClientID", client_id),
                ("OMC", OMC),
                ("OS", OS),
                ("Hash", license_hash),
            ]
        )

        license_res = session.get(
            acquisition_url,
            params=params,
            headers={"User-Agent": UA},
            timeout=args.timeout,
            stream=True,
        )
        try:
            license_res.raise_for_status()
            with open(license_file, "wb") as outfile:
                for chunk in license_res.iter_content(1024):
                    outfile.write(chunk)
            logger.debug(f"Saved license file {license_file}")

        except HTTPError as he:
            if he.response.status_code == 404:
                # odm file has expired
                logger.error(
                    f'The loan file "{args.odm_file}" has expired. Please download again.'
                )
            else:
                logger.error(he.response.content)
            sys.exit(1)
        except ConnectionError as ce:
            logger.error(f"ConnectionError: {str(ce)}")
            sys.exit(1)

    license_xml_doc = xml.etree.ElementTree.parse(license_file)
    license_root = license_xml_doc.getroot()

    ns = "{http://license.overdrive.com/2008/03/License.xsd}"

    license_client = license_root.find(f"{ns}SignedInfo").find(f"{ns}ClientID")
    license_client_id = license_client.text

    with open(license_file, "r", encoding="utf-8") as lic_file:
        lic_file_contents = lic_file.read()

    track_count = 0
    file_tracks = []
    keep_cover = args.always_keep_cover
    audio_lengths_ms = []
    for p in download_parts:
        part_number = int(p["number"])
        part_filename = os.path.join(
            book_folder,
            f"{slugify(f'{title} - Part {part_number:02d}', allow_unicode=True)}.mp3",
        )
        part_tmp_filename = f"{part_filename}.part"
        part_file_size = int(p["filesize"])
        part_url_filename = p["filename"]
        part_download_url = f"{download_baseurl}/{part_url_filename}"
        part_markers = []

        if os.path.isfile(part_filename):
            logger.warning("Already saved %s", colored(part_filename, "magenta"))
        else:
            try:
                already_downloaded_len = 0
                if os.path.exists(part_tmp_filename):
                    already_downloaded_len = os.stat(part_tmp_filename).st_size

                part_download_res = session.get(
                    part_download_url,
                    headers={
                        "User-Agent": UA,
                        "ClientID": license_client_id,
                        "License": lic_file_contents,
                        "Range": f"bytes={already_downloaded_len}-"
                        if already_downloaded_len
                        else None,
                    },
                    timeout=args.timeout,
                    stream=True,
                )
                part_download_res.raise_for_status()

                with tqdm.wrapattr(
                    part_download_res.raw,
                    "read",
                    total=part_file_size,
                    initial=already_downloaded_len,
                    desc=f"Part {part_number:2d}",
                    disable=args.hide_progress,
                ) as res_raw:
                    with open(
                        part_tmp_filename, "ab" if already_downloaded_len else "wb"
                    ) as outfile:
                        shutil.copyfileobj(res_raw, outfile)

                # try to remux file to remove mp3 lame tag errors
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    ffmpeg_loglevel,
                    "-i",
                    part_tmp_filename,
                    "-c:a",
                    "copy",
                    "-c:v",
                    "copy",
                    part_filename,
                ]
                try:
                    exit_code = subprocess.call(cmd)
                    if exit_code:
                        logger.warning(f"ffmpeg exited with the code: {exit_code!s}")
                        logger.warning(f"Command: {' '.join(cmd)!s}")
                        os.rename(part_tmp_filename, part_filename)
                    else:
                        os.remove(part_tmp_filename)
                except Exception as ffmpeg_ex:  # pylint: disable=broad-except
                    logger.warning(f"Error executing ffmpeg: {str(ffmpeg_ex)}")
                    os.rename(part_tmp_filename, part_filename)

            except HTTPError as he:
                logger.error(f"HTTPError: {str(he)}")
                logger.debug(he.response.content)
                sys.exit(1)

            except ConnectionError as ce:
                logger.error(f"ConnectionError: {str(ce)}")
                sys.exit(1)

        try:
            # Fill id3 info for mp3 part
            audiofile = eyed3.load(part_filename)
            write_tags(
                audiofile,
                title,
                authors,
                narrators,
                publisher,
                description,
                cover_bytes,
                part_number,
                len(download_parts),
            )
            audiofile.tag.save()

            audio_lengths_ms.append(mp3_duration_ms(part_filename))

            # Extract OD chapter info from mp3s for use in merged file
            for frame in audiofile.tag.frame_set.get(eyed3.id3.frames.USERTEXT_FID, []):
                if frame.description != "OverDrive MediaMarkers":
                    continue
                if frame.text:
                    try:
                        tree = xml.etree.ElementTree.fromstring(frame.text)
                    except UnicodeEncodeError:
                        tree = xml.etree.ElementTree.fromstring(
                            frame.text.encode("ascii", "ignore").decode("ascii")
                        )

                    for m in tree.iter("Marker"):
                        marker_name = m.find("Name").text.strip()
                        marker_timestamp = m.find("Time").text
                        timestamp = None
                        ts_mark = 0
                        # 2 timestamp formats found
                        for r in ("%M:%S.%f", "%H:%M:%S.%f"):
                            try:
                                timestamp = time.strptime(marker_timestamp, r)
                                ts = datetime.timedelta(
                                    hours=timestamp.tm_hour,
                                    minutes=timestamp.tm_min,
                                    seconds=timestamp.tm_sec,
                                )
                                ts_mark = int(1000 * ts.total_seconds())
                                break
                            except ValueError:
                                pass

                        if not timestamp:
                            # some invalid timestamp string, e.g. 60:15.00
                            mobj = re.match(MARKER_TIMESTAMP_HHMMSS, marker_timestamp)
                            if mobj:
                                ts_mark = (
                                    int(mobj.group("hr")) * 60 * 60 * 1000
                                    + int(mobj.group("min")) * 60 * 1000
                                    + int(mobj.group("sec")) * 1000
                                    + int(mobj.group("ms"))
                                )
                            else:
                                mobj = re.match(MARKER_TIMESTAMP_MMSS, marker_timestamp)
                                if mobj:
                                    ts_mark = (
                                        int(mobj.group("min")) * 60 * 1000
                                        + int(mobj.group("sec")) * 1000
                                        + int(mobj.group("ms"))
                                    )
                                else:
                                    raise ValueError(
                                        f"Invalid marker timestamp: {marker_timestamp}"
                                    )

                        track_count += 1
                        part_markers.append(
                            (f"ch{track_count:02d}", marker_name, ts_mark)
                        )
                break

            if (
                args.add_chapters
                and not args.merge_output
                and not audiofile.tag.table_of_contents
            ):
                # set the chapter marks
                generated_markers = []
                for j, file_marker in enumerate(part_markers):
                    generated_markers.append(
                        {
                            "id": file_marker[0],
                            "text": file_marker[1],
                            "start_time": int(file_marker[2]),
                            "end_time": int(
                                round(audiofile.info.time_secs * 1000)
                                if j == (len(part_markers) - 1)
                                else part_markers[j + 1][2]
                            ),
                        }
                    )

                toc = audiofile.tag.table_of_contents.set(
                    "toc".encode("ascii"),
                    toplevel=True,
                    ordered=True,
                    child_ids=[],
                    description="Table of Contents",
                )

                for i, m in enumerate(generated_markers):
                    title_frameset = eyed3.id3.frames.FrameSet()
                    title_frameset.setTextFrame(
                        eyed3.id3.frames.TITLE_FID, str(m["text"])
                    )

                    chap = audiofile.tag.chapters.set(
                        m["id"].encode("ascii"),
                        times=(m["start_time"], m["end_time"]),
                        sub_frames=title_frameset,
                    )
                    toc.child_ids.append(chap.element_id)
                    start_time = datetime.timedelta(milliseconds=m["start_time"])
                    end_time = datetime.timedelta(milliseconds=m["end_time"])
                    logger.debug(
                        'Added chap tag => %s: %s-%s "%s" to "%s"',
                        colored(m["id"], "cyan"),
                        start_time,
                        end_time,
                        colored(m["text"], "cyan"),
                        colored(part_filename, "blue"),
                    )

                audiofile.tag.save()

        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                "Error saving ID3: %s", colored(str(e), "red", attrs=["bold"])
            )
            keep_cover = True

        logger.info('Saved "%s"', colored(part_filename, "magenta"))

        file_tracks.append(
            {
                "file": part_filename,
                "markers": part_markers,
            }
        )
    # end loop: for p in download_parts:

    debug_meta["audio_lengths_ms"] = audio_lengths_ms
    debug_meta["file_tracks"] = file_tracks

    if args.merge_output:
        logger.info(
            'Generating "%s"...',
            colored(
                book_filename if args.merge_format == "mp3" else book_m4b_filename,
                "magenta",
            ),
        )

        merge_into_mp3(
            book_filename, file_tracks, ffmpeg_loglevel, args.hide_progress, logger
        )

        audiofile = eyed3.load(book_filename)
        write_tags(
            audiofile,
            title,
            authors,
            narrators,
            publisher,
            description,
            cover_bytes,
            0,
            0,
            overwrite_title=True,
        )

        if args.add_chapters and not audiofile.tag.table_of_contents:
            merged_markers = []
            for i, f in enumerate(file_tracks):
                prev_tracks_len_ms = (
                    0 if i == 0 else reduce(lambda x, y: x + y, audio_lengths_ms[0:i])
                )
                this_track_endtime_ms = int(
                    reduce(lambda x, y: x + y, audio_lengths_ms[0 : i + 1])
                )
                file_markers = f["markers"]
                for j, file_marker in enumerate(file_markers):
                    merged_markers.append(
                        {
                            "id": file_marker[0],
                            "text": str(file_marker[1]),
                            "start_time": int(file_marker[2]) + prev_tracks_len_ms,
                            "end_time": int(
                                this_track_endtime_ms
                                if j == (len(file_markers) - 1)
                                else file_markers[j + 1][2] + prev_tracks_len_ms
                            ),
                        }
                    )
            debug_meta["merged_markers"] = merged_markers

            toc = audiofile.tag.table_of_contents.set(
                "toc".encode("ascii"),
                toplevel=True,
                ordered=True,
                child_ids=[],
                description="Table of Contents",
            )

            for i, m in enumerate(merged_markers):
                title_frameset = eyed3.id3.frames.FrameSet()
                title_frameset.setTextFrame(eyed3.id3.frames.TITLE_FID, str(m["text"]))
                chap = audiofile.tag.chapters.set(
                    m["id"].encode("ascii"),
                    times=(m["start_time"], m["end_time"]),
                    sub_frames=title_frameset,
                )
                toc.child_ids.append(chap.element_id)
                start_time = datetime.timedelta(milliseconds=m["start_time"])
                end_time = datetime.timedelta(milliseconds=m["end_time"])
                logger.debug(
                    'Added chap tag => %s: %s-%s "%s" to "%s"',
                    colored(m["id"], "cyan"),
                    start_time,
                    end_time,
                    colored(m["text"], "cyan"),
                    colored(book_filename, "blue"),
                )

        audiofile.tag.save()

        if args.merge_format == "mp3":
            logger.info(
                'Merged files into "%s"',
                colored(
                    book_filename if args.merge_format == "mp3" else book_m4b_filename,
                    "magenta",
                ),
            )

        if args.merge_format == "m4b":
            convert_to_m4b(
                book_filename,
                book_m4b_filename,
                cover_filename,
                ffmpeg_loglevel,
                args.hide_progress,
                logger,
            )

        if not args.keep_mp3:
            for f in file_tracks:
                try:
                    os.remove(f["file"])
                except Exception as e:  # pylint: disable=broad-except
                    logger.warning(f'Error deleting "{f["file"]}": {str(e)}')

    if cleanup_odm_license:
        for target_file in [odm_file, license_file]:
            if os.path.isfile(target_file):
                try:
                    os.remove(target_file)
                except Exception as e:  # pylint: disable=broad-except
                    logger.warning(f'Error deleting "{target_file}": {str(e)}')

    if not keep_cover and os.path.isfile(cover_filename):
        try:
            os.remove(cover_filename)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Error deleting "{cover_filename}": {str(e)}')

    if args.write_json:
        with open(debug_filename, "w", encoding="utf-8") as outfile:
            json.dump(debug_meta, outfile, indent=2)


def process_audiobook_loan(loan, openbook, parsed_toc, session, args, logger):
    """
    Download the audiobook loan directly via Libby without the use of
    an odm file

    :param loan:
    :param openbook:
    :param parsed_toc:
    :param session: From `LibbyClient.libby_session` because it contains a needed auth cookie
    :param args:
    :return:
    """

    ffmpeg_loglevel = "info" if logger.level == logging.DEBUG else "error"
    debug_meta = {}

    title = loan["title"]
    cover_highest_res = next(
        iter(
            sorted(
                list(loan.get("covers").values()),
                key=lambda c: c.get("width", 0),
                reverse=True,
            )
        ),
        None,
    )
    cover_url = cover_highest_res["href"] if cover_highest_res else None
    authors = [
        c["name"] for c in openbook.get("creator", []) if c.get("role", "") == "author"
    ]
    if not authors:
        authors = [
            c["name"]
            for c in openbook.get("creator", [])
            if c.get("role", "") == "editor"
        ]
    if not authors:
        authors = [c["name"] for c in openbook.get("creator", [])]
    narrators = [
        c["name"]
        for c in openbook.get("creator", [])
        if c.get("role", "") == "narrator"
    ]
    publisher = loan.get("publisherAccount", {}).get("name", "") or ""
    description = (
        openbook.get("description", {}).get("full", "")
        or openbook.get("description", {}).get("short")
        or ""
    )
    debug_meta["meta"] = {
        "title": title,
        "coverUrl": cover_url,
        "authors": authors,
        "publisher": publisher,
        "description": description,
    }

    download_parts = list(parsed_toc.values())
    debug_meta["download_parts"] = []
    for p in download_parts:
        chapters = [
            {"title": m.title, "start": m.start_second, "end": m.end_second}
            for m in p["chapters"]
        ]
        debug_meta["download_parts"].append(
            {
                "url": p["url"],
                "audio-duration": p["audio-duration"],
                "file-length": p["file-length"],
                "spine-position": p["spine-position"],
                "chapters": chapters,
            }
        )

    logger.info(
        f'Downloading "{colored(title, "blue", attrs=["bold"])}" '
        f'by "{colored(", ".join(authors), "blue", attrs=["bold"])}" '
        f"in {len(download_parts)} part(s)..."
    )

    book_folder, book_filename, book_m4b_filename = generate_names(title, authors, args)

    # check early if a merged file is already saved
    if args.merge_output and os.path.isfile(
        book_filename if args.merge_format == "mp3" else book_m4b_filename
    ):
        logger.warning(
            'Already saved "%s"',
            colored(
                book_filename if args.merge_format == "mp3" else book_m4b_filename,
                "magenta",
            ),
        )
        sys.exit()

    debug_filename = os.path.join(book_folder, "debug.json")

    cover_filename, cover_bytes = generate_cover(
        book_folder, cover_url, session, args.timeout, logger
    )

    keep_cover = args.always_keep_cover
    file_tracks = []
    for p in download_parts:
        part_number = p["spine-position"] + 1
        part_filename = os.path.join(
            book_folder,
            f"{slugify(f'{title} - Part {part_number:02d}', allow_unicode=True)}.mp3",
        )
        part_tmp_filename = f"{part_filename}.part"
        part_file_size = p["file-length"]
        part_download_url = p["url"]

        if os.path.isfile(part_filename):
            logger.warning("Already saved %s", colored(part_filename, "magenta"))
        else:
            try:
                already_downloaded_len = 0
                if os.path.exists(part_tmp_filename):
                    already_downloaded_len = os.stat(part_tmp_filename).st_size

                part_download_res = session.get(
                    part_download_url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Range": f"bytes={already_downloaded_len}-"
                        if already_downloaded_len
                        else None,
                    },
                    timeout=args.timeout,
                    stream=True,
                )
                part_download_res.raise_for_status()

                with tqdm.wrapattr(
                    part_download_res.raw,
                    "read",
                    total=part_file_size,
                    initial=already_downloaded_len,
                    desc=f"Part {part_number:2d}",
                    disable=args.hide_progress,
                ) as res_raw:
                    with open(
                        part_tmp_filename, "ab" if already_downloaded_len else "wb"
                    ) as outfile:
                        shutil.copyfileobj(res_raw, outfile)

                # try to remux file to remove mp3 lame tag errors
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    ffmpeg_loglevel,
                    "-i",
                    part_tmp_filename,
                    "-c:a",
                    "copy",
                    "-c:v",
                    "copy",
                    part_filename,
                ]
                try:
                    exit_code = subprocess.call(cmd)
                    if exit_code:
                        logger.warning(f"ffmpeg exited with the code: {exit_code!s}")
                        logger.warning(f"Command: {' '.join(cmd)!s}")
                        os.rename(part_tmp_filename, part_filename)
                    else:
                        os.remove(part_tmp_filename)
                except Exception as ffmpeg_ex:  # pylint: disable=broad-except
                    logger.warning(f"Error executing ffmpeg: {str(ffmpeg_ex)}")
                    os.rename(part_tmp_filename, part_filename)

            except HTTPError as he:
                logger.error(f"HTTPError: {str(he)}")
                logger.debug(he.response.content)
                sys.exit(1)

            except ConnectionError as ce:
                logger.error(f"ConnectionError: {str(ce)}")
                sys.exit(1)

        try:
            # Fill id3 info for mp3 part
            audiofile = eyed3.load(part_filename)
            write_tags(
                audiofile,
                title,
                authors,
                narrators,
                publisher,
                description,
                cover_bytes,
                part_number,
                len(download_parts),
            )
            audiofile.tag.save()

            if (
                args.add_chapters
                and not args.merge_output
                and not audiofile.tag.table_of_contents
            ):
                toc = audiofile.tag.table_of_contents.set(
                    "toc".encode("ascii"),
                    toplevel=True,
                    ordered=True,
                    child_ids=[],
                    description="Table of Contents",
                )
                chapter_marks = p["chapters"]
                for i, m in enumerate(chapter_marks):
                    title_frameset = eyed3.id3.frames.FrameSet()
                    title_frameset.setTextFrame(eyed3.id3.frames.TITLE_FID, m.title)
                    chap = audiofile.tag.chapters.set(
                        f"ch{i}".encode("ascii"),
                        times=(m.start_second * 1000, m.end_second * 1000),
                        sub_frames=title_frameset,
                    )
                    toc.child_ids.append(chap.element_id)
                    start_time = datetime.timedelta(seconds=m.start_second)
                    end_time = datetime.timedelta(seconds=m.end_second)
                    logger.debug(
                        'Added chap tag => %s: %s-%s "%s" to "%s"',
                        colored(f"ch{i}", "cyan"),
                        start_time,
                        end_time,
                        colored(m.title, "cyan"),
                        colored(part_filename, "blue"),
                    )
                audiofile.tag.save()

        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                "Error saving ID3: %s", colored(str(e), "red", attrs=["bold"])
            )
            keep_cover = True

        logger.info('Saved "%s"', colored(part_filename, "magenta"))
        file_tracks.append({"file": part_filename})

    debug_meta["file_tracks"] = file_tracks
    if args.merge_output:
        logger.info(
            'Generating "%s"...',
            colored(
                book_filename if args.merge_format == "mp3" else book_m4b_filename,
                "magenta",
            ),
        )

        merge_into_mp3(
            book_filename, file_tracks, ffmpeg_loglevel, args.hide_progress, logger
        )

        audiofile = eyed3.load(book_filename)
        write_tags(
            audiofile,
            title,
            authors,
            narrators,
            publisher,
            description,
            cover_bytes,
            0,
            0,
        )

        if args.add_chapters and not audiofile.tag.table_of_contents:
            toc = audiofile.tag.table_of_contents.set(
                "toc".encode("ascii"),
                toplevel=True,
                ordered=True,
                child_ids=[],
                description="Table of Contents",
            )
            merged_markers = merge_toc(parsed_toc)
            debug_meta["merged_markers"] = [
                {"title": m.title, "start": m.start_second, "end": m.end_second}
                for m in merged_markers
            ]

            for i, m in enumerate(merged_markers):
                title_frameset = eyed3.id3.frames.FrameSet()
                title_frameset.setTextFrame(eyed3.id3.frames.TITLE_FID, m.title)
                chap = audiofile.tag.chapters.set(
                    f"ch{i}".encode("ascii"),
                    times=(m.start_second * 1000, m.end_second * 1000),
                    sub_frames=title_frameset,
                )
                toc.child_ids.append(chap.element_id)
                start_time = datetime.timedelta(seconds=m.start_second)
                end_time = datetime.timedelta(seconds=m.end_second)
                logger.debug(
                    'Added chap tag => %s: %s-%s "%s" to "%s"',
                    colored(f"ch{i}", "cyan"),
                    start_time,
                    end_time,
                    colored(m.title, "cyan"),
                    colored(book_filename, "blue"),
                )

        audiofile.tag.save()

        if args.merge_format == "mp3":
            logger.info(
                'Merged files into "%s"',
                colored(
                    book_filename if args.merge_format == "mp3" else book_m4b_filename,
                    "magenta",
                ),
            )

        if args.merge_format == "m4b":
            convert_to_m4b(
                book_filename,
                book_m4b_filename,
                cover_filename,
                ffmpeg_loglevel,
                args.hide_progress,
                logger,
            )

        if not args.keep_mp3:
            for f in file_tracks:
                try:
                    os.remove(f["file"])
                except Exception as e:  # pylint: disable=broad-except
                    logger.warning(f'Error deleting "{f["file"]}": {str(e)}')

    if not keep_cover and os.path.isfile(cover_filename):
        try:
            os.remove(cover_filename)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Error deleting "{cover_filename}": {str(e)}')

    if args.write_json:
        with open(debug_filename, "w", encoding="utf-8") as outfile:
            json.dump(debug_meta, outfile, indent=2)