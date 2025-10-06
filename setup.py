#!/usr/bin/env python3
"""
Setup script for CloudEdge API library.
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="pycloudedge",
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
    author="Francesco D'Aloisio",
    description="Python library for CloudEdge cameras",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/fradaloisio/pycloudedge",
    project_urls={
        "Bug Tracker": "https://github.com/fradaloisio/pycloudedge/issues",
        "Documentation": "https://github.com/fradaloisio/pycloudedge",
        "Source Code": "https://github.com/fradaloisio/pycloudedge",
    },
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Home Automation",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.7",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=6.0",
            "pytest-asyncio>=0.18.0",
            "black>=21.0.0",
            "flake8>=3.8.0",
            "mypy>=0.910",
            "twine>=3.4.0",
            "build>=0.7.0",
        ],
    },
    keywords="cloudedge smarteye cameras iot api home-automation",
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "cloudedge=cloudedge.cli:main",
        ],
    },
)