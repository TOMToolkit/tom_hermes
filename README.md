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
        'DATA_CONVERTER_CLASS': 'custom_code.hermes.MyDataConversionClass' # Optional: Defaults to 'tom_hermes.publisher.HermesDataConverter'
    }
```

You can add `HERMES_BASE_URL` to your `settings.py` if you want to point to a hermes instance other than `https://hermes.lco.global`
and `DEFAULT_TOPIC` if you want messages to default to a topic other than `'tomtoolkit.test'`.

### Custom Data
If you use custom data models, you can also create your own `DATA_CONVERTER_CLASS`. This setting should be the dot separated path to a class that converts your custom `ReducedDatum` or `Target` into a format understood by the [HERMES API](https://hermes.lco.global/about).


`Hermes` is now available as a Data Service from the nav bar. You may configure and execute your queries as you would any Data Service.
