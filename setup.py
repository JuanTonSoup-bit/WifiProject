"""
Installable package definition for the PC-side pipeline.

Run `pip install -e .` from the repo root to install in editable mode.
This lets you run `python -m pc.main` from anywhere and imports resolve correctly.
"""

from setuptools import setup, find_packages

setup(
    name="wifi-csi-detector",
    version="0.1.0",
    description="Real-time WiFi CSI motion detection pipeline",
    python_requires=">=3.9",
    packages=find_packages(exclude=["tests*", "pi*", "laptop*", "docs*"]),
    install_requires=[
        "numpy>=1.24.0",
        "PyYAML>=6.0",
        "matplotlib>=3.7.0",
    ],
    extras_require={
        "full": [
            "scipy>=1.11.0",
            "flask>=2.3.0",
            "colorlog>=6.7.0",
        ],
        "dev": [
            "scipy>=1.11.0",
            "flask>=2.3.0",
            "colorlog>=6.7.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "csi-detector=pc.main:main",
        ],
    },
)
