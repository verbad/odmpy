import glob
import json
import os.path
import shutil
import unittest
import warnings
from datetime import datetime

import ebooklib
import responses
from bs4 import BeautifulSoup
from ebooklib import epub

from odmpy.errors import LibbyNotConfiguredError
from odmpy.libby import LibbyClient, LibbyFormats
from odmpy.odm import run


# Test non-interactive options
class OdmpyLibbyTests(unittest.TestCase):
    def setUp(self) -> None:
        warnings.filterwarnings(
            action="ignore", message="unclosed", category=ResourceWarning
        )
        self.test_data_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data"
        )
        self.test_downloads_dir = os.path.join(self.test_data_dir, "downloads")
        if not os.path.isdir(self.test_downloads_dir):
            os.makedirs(self.test_downloads_dir)

    def tearDown(self) -> None:
        if os.path.isdir(self.test_downloads_dir):
            shutil.rmtree(self.test_downloads_dir, ignore_errors=True)

    def test_libby_export(self):
        """
        `odmpy libby --exportloans`
        """
        try:
            run(["libby", "--check"], be_quiet=True)
        except LibbyNotConfiguredError:
            self.skipTest("Libby not setup.")

        loans_file_name = os.path.join(
            self.test_downloads_dir,
            f"test_loans_{int(datetime.utcnow().timestamp()*1000)}.json",
        )
        run(["libby", "--exportloans", loans_file_name], be_quiet=True)
        self.assertTrue(os.path.exists(loans_file_name))
        with open(loans_file_name, "r", encoding="utf-8") as f:
            loans = json.load(f)
            for loan in loans:
                self.assertIn("id", loan)

    @unittest.skip("Takes too long")  # turn on/off at will
    def test_libby_download_select(self):
        """
        `odmpy libby --select N`
        """
        try:
            run(["libby", "--check"], be_quiet=True)
        except LibbyNotConfiguredError:
            self.skipTest("Libby not setup.")

        ts = int(datetime.utcnow().timestamp() * 1000)
        loans_file_name = os.path.join(self.test_downloads_dir, f"test_loans_{ts}.json")
        download_folder = os.path.join(self.test_downloads_dir, f"test_downloads_{ts}")
        os.makedirs(download_folder)
        run(["libby", "--exportloans", loans_file_name], be_quiet=True)
        self.assertTrue(os.path.exists(loans_file_name))
        with open(loans_file_name, "r", encoding="utf-8") as f:
            loans = json.load(f)
        if not loans:
            self.skipTest("No loans.")

        try:
            run(
                [
                    "--noversioncheck",
                    "libby",
                    "--direct",
                    "--downloaddir",
                    download_folder,
                    "--select",
                    str(len(loans)),
                    "--hideprogress",
                ],
                be_quiet=True,
            )
        except KeyboardInterrupt:
            self.fail("Test aborted")

        self.assertTrue(glob.glob(f"{download_folder}/*/*.mp3"))

    @unittest.skip("Takes too long")  # turn on/off at will
    def test_libby_download_latest(self):
        """
        `odmpy libby --latest N`
        """
        try:
            run(["libby", "--check"], be_quiet=True)
        except LibbyNotConfiguredError:
            self.skipTest("Libby not setup.")
        ts = int(datetime.utcnow().timestamp() * 1000)
        loans_file_name = os.path.join(self.test_downloads_dir, f"test_loans_{ts}.json")
        download_folder = os.path.join(self.test_downloads_dir, f"test_downloads_{ts}")
        os.makedirs(download_folder)
        run(["libby", "--exportloans", loans_file_name], be_quiet=True)
        self.assertTrue(os.path.exists(loans_file_name))
        with open(loans_file_name, "r", encoding="utf-8") as f:
            loans = json.load(f)
        if not loans:
            self.skipTest("No loans.")

        try:
            run(
                [
                    "--noversioncheck",
                    "libby",
                    "--direct",
                    "--downloaddir",
                    download_folder,
                    "--latest",
                    "1",
                    "--hideprogress",
                ],
                be_quiet=True,
            )
        except KeyboardInterrupt:
            self.fail("Test aborted")

        self.assertTrue(glob.glob(f"{download_folder}/*/*.mp3"))

    @unittest.skip("Takes too long")  # turn on/off at will
    def test_libby_download_ebook(self):
        """
        `odmpy libby --ebooks --select N`
        """
        try:
            run(["libby", "--check"], be_quiet=True)
        except LibbyNotConfiguredError:
            self.skipTest("Libby not setup.")
        ts = int(datetime.utcnow().timestamp() * 1000)
        loans_file_name = os.path.join(self.test_downloads_dir, f"test_loans_{ts}.json")
        download_folder = os.path.join(self.test_downloads_dir, f"test_downloads_{ts}")
        os.makedirs(download_folder)
        run(["libby", "--ebooks", "--exportloans", loans_file_name], be_quiet=True)
        self.assertTrue(os.path.exists(loans_file_name))
        with open(loans_file_name, "r", encoding="utf-8") as f:
            loans = json.load(f)
        if not loans:
            self.skipTest("No loans.")

        selected_index = 0
        for i, loan in enumerate(loans, start=1):
            if LibbyClient.get_loan_format(
                loan
            ) == LibbyFormats.EBookEPubAdobe and LibbyClient.has_format(
                loan, LibbyFormats.EBookOverdrive
            ):
                selected_index = i
                break
        if not selected_index:
            self.skipTest("No suitable ebook loan.")

        try:
            run(
                [
                    "libby",
                    "--ebooks",
                    "--downloaddir",
                    download_folder,
                    "--select",
                    str(selected_index),
                ],
                be_quiet=True,
            )
        except KeyboardInterrupt:
            self.fail("Test aborted")

        acsm_file = glob.glob(f"{download_folder}/*/*.acsm")
        self.assertTrue(acsm_file)

    @unittest.skip("Takes too long")  # turn on/off at will
    def test_libby_download_ebook_direct(self):
        """
        `odmpy libby --ebooks --select N`
        """
        try:
            run(["libby", "--check"], be_quiet=True)
        except LibbyNotConfiguredError:
            self.skipTest("Libby not setup.")
        ts = int(datetime.utcnow().timestamp() * 1000)
        loans_file_name = os.path.join(self.test_downloads_dir, f"test_loans_{ts}.json")
        download_folder = os.path.join(self.test_downloads_dir, f"test_downloads_{ts}")
        os.makedirs(download_folder)
        run(["libby", "--ebooks", "--exportloans", loans_file_name], be_quiet=True)
        self.assertTrue(os.path.exists(loans_file_name))
        with open(loans_file_name, "r", encoding="utf-8") as f:
            loans = json.load(f)
        if not loans:
            self.skipTest("No loans.")

        selected_index = 0
        for i, loan in enumerate(loans, start=1):
            if LibbyClient.get_loan_format(
                loan
            ) == LibbyFormats.EBookEPubAdobe and LibbyClient.has_format(
                loan, LibbyFormats.EBookOverdrive
            ):
                selected_index = i
                break
        if not selected_index:
            self.skipTest("No suitable ebook loan.")

        try:
            run(
                [
                    "libby",
                    "--ebooks",
                    "--direct",
                    "--downloaddir",
                    download_folder,
                    "--select",
                    str(selected_index),
                    # "--hideprogress",
                ],
                be_quiet=True,
            )
        except KeyboardInterrupt:
            self.fail("Test aborted")

        epub_file = glob.glob(f"{download_folder}/*/*.epub")
        self.assertTrue(epub_file)

    @responses.activate
    def test_mock_libby_download_magazine(self):
        settings_folder = os.path.join(self.test_downloads_dir, "settings")
        if not os.path.exists(settings_folder):
            os.makedirs(settings_folder)

        # generate fake settings
        with open(
            os.path.join(settings_folder, "libby.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(
                {
                    "chip": "12345",
                    "identity": "abcdefgh",
                    "syncable": False,
                    "primary": True,
                    "__libby_sync_code": "12345678",
                },
                f,
            )

        with open(
            os.path.join(self.test_data_dir, "magazine", "sync.json"),
            "r",
            encoding="utf-8",
        ) as s:
            responses.get(
                "https://sentry-read.svc.overdrive.com/chip/sync", json=json.load(s)
            )
        with open(
            os.path.join(self.test_data_dir, "magazine", "rosters.json"),
            "r",
            encoding="utf-8",
        ) as r:
            responses.get(
                "http://localhost/mock/rosters.json",
                json=json.load(r),
            )
        with open(
            os.path.join(self.test_data_dir, "magazine", "openbook.json"),
            "r",
            encoding="utf-8",
        ) as o:
            responses.get(
                "http://localhost/mock/openbook.json",
                json=json.load(o),
            )
        responses.head(
            "http://localhost/mock",
            body="",
        )
        responses.get(
            "https://sentry-read.svc.overdrive.com/open/magazine/card/123456789/title/9999999",
            json={
                "message": "xyz",
                "urls": {
                    "web": "http://localhost/mock",
                    "rosters": "http://localhost/mock/rosters.json",
                    "openbook": "http://localhost/mock/openbook.json",
                },
            },
        )
        with open(
            os.path.join(self.test_data_dir, "magazine", "media.json"),
            "r",
            encoding="utf-8",
        ) as m:
            responses.get(
                "https://thunder.api.overdrive.com/v2/media/9999999?x-client-id=dewey",
                json=json.load(m),
            )
        with open(os.path.join(self.test_data_dir, "magazine", "cover.jpg"), "rb") as c:
            # this is the cover from OD API
            responses.get(
                "http://localhost/mock/cover.jpg",
                content_type="image/jpeg",
                body=c.read(),
            )
        # mock roster title contents
        for page in (
            "pages/Cover.xhtml",
            "stories/story-01.xhtml",
            "stories/story-02.xhtml",
        ):
            with open(
                os.path.join(self.test_data_dir, "magazine", "content", page),
                "r",
                encoding="utf-8",
            ) as f:
                responses.get(
                    f"http://localhost/{page}",
                    content_type="application/xhtml+xml",
                    body=f.read(),
                )
        for img in ("assets/cover.jpg",):
            with open(
                os.path.join(self.test_data_dir, "magazine", "content", img), "rb"
            ) as f:
                responses.get(
                    f"http://localhost/{img}",
                    content_type="image/jpeg",
                    body=f.read(),
                )

        run(
            [
                "libby",
                "--settings",
                settings_folder,
                "--magazines",
                "--downloaddir",
                self.test_downloads_dir,
                "--bookfolderformat",
                "test",
                "--bookfileformat",
                "magazine",
                "--latest",
                "1",
                "--opf",
                "--hideprogress",
            ],
            be_quiet=True,
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(self.test_downloads_dir, "test", "magazine.opf")
            )
        )
        epub_file_path = os.path.join(self.test_downloads_dir, "test", "magazine.epub")
        self.assertTrue(os.path.exists(epub_file_path))

        book = epub.read_epub(epub_file_path, {"ignore_ncx": True})
        stories = [
            d
            for d in list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            if d.get_name().startswith("stories/")
        ]
        self.assertEqual(len(stories), 2)
        for story in stories:
            soup = BeautifulSoup(story.get_content(), "html.parser")
            self.assertTrue(
                soup.find("h1")
            )  # check that pages are properly de-serialised

        cover = next(
            iter([b for b in list(book.get_items_of_type(ebooklib.ITEM_COVER))]), None
        )
        self.assertTrue(cover)
        with open(
            os.path.join(
                self.test_data_dir, "magazine", "content", "assets", "cover.jpg"
            ),
            "rb",
        ) as f:
            self.assertEqual(f.read(), cover.get_content())

        nav = next(
            iter([b for b in list(book.get_items_of_type(ebooklib.ITEM_NAVIGATION))]),
            None,
        )
        self.assertTrue(nav)
