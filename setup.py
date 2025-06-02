import glob
import os.path
from setuptools import setup

# Read requirements.txt
with open("requirements.txt") as f:
    requirements = f.read().splitlines()

ui_files = [
    os.path.relpath(f, "histoqc/ui/")
    for f in glob.iglob("histoqc/ui/**/*", recursive=True)
    if os.path.isfile(f)
]
data_files = [
    os.path.relpath(f, "histoqc/data/")
    for folder in ['models', 'pen', 'templates']
    for f in glob.iglob(f"histoqc/data/{folder}/**/*", recursive=True)
    if os.path.isfile(f)
]

setup(
    name="histoqc",
    version="1.0",
    install_requires=requirements,
    package_data={
            'histoqc.config': ['*.ini'],
            'histoqc.data': data_files,
            'histoqc.ui': ui_files,
        }
)