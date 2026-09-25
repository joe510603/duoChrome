"""duochrome — setup script (legacy mode for setuptools <61).

Metadata lives here (not in pyproject.toml) because the user's setuptools
58.x doesn't support PEP 621 [project] table.
"""
from setuptools import setup, find_packages

setup(
    name="duochrome",
    version="0.1.0",
    description="Lightweight multi-profile browser isolation — like AdsPower/BitBrowser for solo devs",
    long_description="Open N independent Chromium instances, each with its own cookies/cache/storage.",
    python_requires=">=3.9",
    packages=find_packages(include=["duochrome*"]),
    install_requires=[
        "playwright>=1.40",
        "typer>=0.9",
        "fastapi>=0.100",
        "uvicorn>=0.20",
        "pywebview>=5.0",
    ],
    entry_points={
        "console_scripts": [
            "duochrome=duochrome.cli:app",
        ],
    },
    include_package_data=True,
)