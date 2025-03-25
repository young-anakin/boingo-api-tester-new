from setuptools import setup, find_packages

setup(
    name="boingo-api-tester",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        'celery',
        'redis',
        'openai',
        'playwright',
        'tiktoken',
    ],
) 