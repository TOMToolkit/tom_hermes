[![pypi](https://img.shields.io/pypi/v/tom-hermes.svg)](https://pypi.python.org/pypi/tom-hermes)

# tom_hermes
This module adds the [Hermes](https://hermes.lco.global) dataservice to the TOM Toolkit. 
Using this module, TOMs can query targets in the Hermes alert archive.

## Installation

Install the module into your TOM environment:

    pip install tom-hermes


Add `tom_hermes` to your `settings.INSTALLED_APPS`:

```python
    INSTALLED_APPS = TOMTOOKIT_INSTALLED_APPS + [
    'custom_code',
    ...
    'tom_hermes',

    ]
```

## Configuration

Configure your app with TOM-wide credentials by adding the following to your `settings.py` and setting your
`HERMES_API_TOKEN` in your environment:

```python
    HERMES_CONFIGURATION = {
        'HERMES_API_TOKEN': os.getenv('HERMES_API_TOKEN', None),  # Defaults here if user API token not set
        'HERMES_BASE_URL': 'https://hermes-dev.lco.global/', # Optional: Defaults to `https://hermes.lco.global/`
        'DEFAULT_TOPIC': 'myfavorite.topic',  # Optional: Defaults to 'tomtoolkit.test'
    }
```

You can add `HERMES_BASE_URL` to your `settings.py` if you want to point to a HERMES instance other than `https://hermes.lco.global` for submission testing. Retrieving targets from the HERMES Data Service will continue to use https://hermes.lco.global/.
and `DEFAULT_TOPIC` if you want messages to default to a topic other than `'tomtoolkit.test'`.

Users can also add their personal credentials via their profile. If you wish to rely on the defaults above and/or user credentials, then a `settings.HERMES_CONFIGURATION` configuration dictionary is not required.

`Hermes` is now available as a Data Service from the "Data Services" nav-bar menu. You may configure and execute your queries as you would any Data Service.
