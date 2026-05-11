"""
HERMES publication.

### Package layout

- ``tom_hermes.sharing.publisher`` — HTTP calls, message assembly, and the
  ``HermesDataConverter``. Also owns ``BuildHermesMessage`` and
  ``HermesMessageException``.
- ``tom_hermes.sharing.backend`` — ``HermesSharingBackend``, the SharingBackend
  plug-in discovered by ``tom_common.sharing.get_sharing_backends()``.

### Why split along publisher / backend (and why credentials is outside)

The SharingBackend plug-in (``backend.py``) is the public-facing surface
that other apps interact with. The publisher is the HTTP / data-mapping
layer underneath. Separating them keeps the plug-in module small and
focused on the SharingBackend contract, and makes the HTTP code easier
to unit-test in isolation.

Credentials live at ``tom_hermes.credentials`` — NOT inside this package —
because credential resolution is not a sharing-only concern. The same
function is (or will be) used by ``tom_hermes.alertstreams`` and possibly
``tom_hermes.dataservices``.

### Public re-exports

This ``__init__`` re-exports the public names from ``publisher`` and
``backend`` so callers can write ``from tom_hermes.sharing import X``
without caring which submodule ``X`` lives in. That also lets
``settings.DATA_SHARING['hermes']['DATA_CONVERTER_CLASS']`` reference
``tom_hermes.sharing.HermesDataConverter`` as a stable public path.
"""
# Re-export the public surface of the package. Consumers should import
# these names from ``tom_hermes.sharing`` rather than reaching into the
# submodules directly.
from tom_hermes.sharing.backend import HermesSharingBackend
from tom_hermes.sharing.publisher import (
    BuildHermesMessage,
    HermesDataConverter,
    HermesMessageException,
    convert_astropy_brightness_unit_to_hermes,
    convert_astropy_flux_unit_to_hermes,
    convert_astropy_wavelength_unit_to_hermes,
    create_hermes_alert,
    get_hermes_data_converter_class,
    get_hermes_topics,
    preload_to_hermes,
    publish_to_hermes,
)

# The list of public names exported from this package. Kept explicit so
# that flake8's F401 does not flag the imports above and so ``from
# tom_hermes.sharing import *`` is well-defined (here).
__all__ = [
    'BuildHermesMessage',
    'HermesDataConverter',
    'HermesMessageException',
    'HermesSharingBackend',
    'convert_astropy_brightness_unit_to_hermes',
    'convert_astropy_flux_unit_to_hermes',
    'convert_astropy_wavelength_unit_to_hermes',
    'create_hermes_alert',
    'get_hermes_data_converter_class',
    'get_hermes_topics',
    'preload_to_hermes',
    'publish_to_hermes',
]
