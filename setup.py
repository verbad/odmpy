# Copyright (C) 2018 github.com/ping
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

from setuptools import setup  # type: ignore[import]

__author__ = "ping"
__url__ = "https://github.com/ping/odmpy/"
__version__ = "0.8.1"  # also update odmpy/odm.py


__long_description__ = """
``odmpy`` is a console manager for OverDrive audiobook loan files (.odm).
"""

install_requires = [
    "requests>=2.28.0",
    "eyed3>=0.9.7",
    "mutagen>=1.46.0",
    "termcolor>=2.0.0",
    "tqdm>=4.63.0",
    "beautifulsoup4>=4.11.0",
    "lxml>=4.9.0",
    "iso639-lang>=2.1.0",
]

setup(
    name="odmpy",
    version=__version__,
    author=__author__,
    license="GPL",
    url=__url__,
    packages=["odmpy", "odmpy.processing"],
    entry_points={
        "console_scripts": [
            "odmpy = odmpy.__main__:main",
        ]
    },
    python_requires=">=3.8",
    install_requires=install_requires,
    include_package_data=True,
    platforms="any",
    long_description=__long_description__,
    keywords="overdrive audiobook",
    description="A console downloader for an OverDrive audiobook loan.",
    classifiers=[
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Environment :: Console",
        "Intended Audience :: End Users/Desktop",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
