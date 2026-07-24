from setuptools import setup, Extension

module = Extension("searchengine", sources=["searchmodule.c"])

setup(
    name="searchengine",
    version="1.0",
    description="Moteur de recherche full-text en C pour le reseau social",
    ext_modules=[module],
)
