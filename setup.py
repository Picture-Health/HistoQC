from setuptools import setup

# Read requirements.txt
with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="histoqc",
    version="1.0",
    install_requires=requirements,
)