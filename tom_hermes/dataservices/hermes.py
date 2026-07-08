from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

import requests
from django.conf import settings

from tom_dataservices.dataservices import DataService, NotConfiguredError

from tom_hermes import __version__
from tom_hermes.credentials import resolve_hermes_credentials
from tom_hermes.forms import HermesForm

from tom_targets.models import Target
from tom_dataproducts.models import PhotometryReducedDatum

logger = logging.getLogger(__name__)


class HermesDataService(DataService):
    """Query the HERMES Dataservice
    """
    name = 'Hermes'
    verbose_name = 'HERMES Messaging Service'
    info_url = 'https://hermes.lco.global/about'
    base_url = 'https://hermes.lco.global'
    query_results_table = 'tom_hermes/partials/hermes_query_results_table.html'
    app_version = __version__
    app_link = 'https://github.com/TOMToolkit/tom_hermes'

    @classmethod
    def get_form_class(cls):
        """Return the query form class the framework uses to render the query UI.

        Called by ``tom_dataservices.views.DataServiceQueryCreateView.get_form_class``.
        """
        return HermesForm

    @classmethod
    def configuration(cls):
        """Returns the configuration dictionary for tom_hermes
        """
        try:
            return settings.HERMES_CONFIGURATION
        except AttributeError as e:
            raise NotConfiguredError(e)
        except KeyError as e:
            raise NotConfiguredError(
                f"""tom_hermes is not configured.
                    </br>
                    Please see the <a href="{cls.app_link}" target="_blank">documentation</a> for more information.
                """
            )

    @classmethod
    def get_credentials(cls, user=None, **kwargs):
        """Returns the credentials tom_hermes."""
        return resolve_hermes_credentials(user)

    def build_headers(self, *args, **kwargs):
        """Hermes API requests require header: ``Authorization: Token <api_key>``
        """
        creds = self.get_credentials(self.user)
        api_key = creds.get('api_key')
        if not api_key:
            raise NotConfiguredError(
                f"""tom_hermes is not configured. Either user credentials or TOM-wide credentials are required.
                    </br>
                    Please see the <a href="{self.app_link}" target="_blank">documentation</a> for more information.
                """
            )
        return {'Authorization': f'Token {api_key}'}

    @classmethod
    def urls(cls, **kwargs) -> dict:
        """Return the dict of URLs this DataService uses, keyed by purpose.
        """
        base = cls.base_url  # base_url is class attribute

        urls_by_purpose = {
            'base_url': base,
            'info_url': cls.info_url,  # also class attribute

            'query_url': f'{base}/api/v0/query',  # Generic message search (wraps archive-api), returns msg meta-data
            'target_url': f'{base}/api/v0/targets/',

            'message_url_template': f'{base}/api/v0/query/message/{{uuid}}/',  # returns full message
        }
        return urls_by_purpose

    def build_query_parameters(self, parameters, **kwargs):
        """Translate cleaned form data into HERMES ``/target`` URL parameters.
        """
        query_parameters: dict = {}
        if parameters.get('exact_name'):
            query_parameters['name_exact'] = parameters['exact_name']
        if parameters.get('target_name'):
            query_parameters['name'] = parameters['target_name']
        if parameters.get('uuid'):
            query_parameters['referenced_by_uuid'] = parameters['uuid']
        if parameters.get('ra') and parameters.get('dec') and parameters.get('radius'):
            query_parameters['cone_search'] = (f'{parameters.get("ra")}, {parameters.get("dec")}, '
                                               f'{parameters.get("radius")}')
        self.query_parameters = query_parameters
        return query_parameters

    def query_service(self, data, **kwargs):
        """Send the query to HERMES and cache the response on ``self.query_results``.

        Required abstract method
        """
        response = requests.get(
            self.get_urls(url_type='target_url'),
            params=data,
            headers=self.build_headers(),
            timeout=30,
        )
        response.raise_for_status()
        self.query_results = response.json()
        return self.query_results

    def query_targets(self, query_parameters, **kwargs) -> list:
        """
        Specialized query to retrieve targets
        """
        # call query_service if we haven't already
        if not self.query_results:
            self.query_service(query_parameters, **kwargs)

        targets_results = self.query_results['results']

        return targets_results

    def create_target_from_query(self, target_result, **kwargs):
        """Create a new target from a single instance of the target results.
        :param target_result: dictionary describing target details based on query result
        :returns: target object
        :rtype: `Target`
        """

        if target_result.get('right_ascension') and target_result.get('declination'):
            target_type = 'SIDEREAL'
        else:
            target_type = 'NON_SIDEREAL'
        target = Target(
            name=target_result['name'],
            type=target_type,
            ra=target_result.get('right_ascension'),
            dec=target_result.get('declination'),
            pm_ra=target_result.get('pm_ra'),
            pm_dec=target_result.get('pm_dec'),
            epoch_of_elements=target_result.get('epoch_of_elements'),
            mean_anomaly=target_result.get('mean_anomaly'),
            arg_of_perihelion=target_result.get('argument_of_the_perihelion'),
            eccentricity=target_result.get('eccentricity'),
            lng_asc_node=target_result.get('longitude_of_the_ascending_node'),
            inclination=target_result.get('orbital_inclination'),
            semimajor_axis=target_result.get('semimajor_axis'),
            epoch_of_perihelion=target_result.get('epoch_of_perihelion'),
            )
        return target

    def build_query_parameters_from_target(self, target, **kwargs):
        """
        This is a method that builds query parameters based on an existing target object that will be recognized by
        `query_service()` using an exact name match.

        :param target: A target object to be queried
        :return: query_parameters (usually a dict) that can be understood by `query_service()`
        """
        query_parameters = self.build_query_parameters(parameters={'exact_name': target.name})
        return query_parameters

    def query_aliases(self, query_parameters=None, target=None, **kwargs) -> List:
        """
        Set up and run a specialized query for retrieving target names from Hermes. For some reason these names are
        retrievable from the full message Target table, but not the target API itself.

        :param query_parameters: This is the output from build_query_parameters()
        :return: A list of target names
        :rtype: List
        """

        # Get the full message for a specific target via the UUIDs associated with that target.
        target_parameters = self.build_query_parameters_from_target(target)
        target_results = self.query_service(target_parameters)['results']
        alias_results = []
        for target_result in target_results:
            for message in target_result['messages']:
                uuid = message['uuid']
                full_message = self._fetch_full_message(uuid)
                target_table = full_message.get('message', {}).get('data', {}).get('targets', [])
                # Find the appropriate target in the target table and return aliases
                for target_obj in target_table:
                    if target_obj['name'] == target_result['name']:
                        alias_results += target_obj.get('aliases', [])
        return alias_results

    def query_photometry(self, query_parameters, **kwargs):
        """Set up and run a specialized query for a DataService’s photometry service.
        :returns: photometry_results
        :rtype: Usually a List of Dictionaries
        """

        target_results = self.query_service(query_parameters)['results']
        photometry_results = []
        for target_result in target_results:
            for message in target_result['messages']:
                uuid = message['uuid']
                full_message = self._fetch_full_message(uuid)
                message_phot = full_message.get('message', {}).get('data', {}).get('photometry', [])
                for phot in message_phot:
                    if phot['target_name'] == target_result['name']:
                        photometry_results.append(phot)
        return photometry_results

    def create_reduced_datums_from_query(self, target, data=[], data_type='photometry', **kwargs):
        """
        Create and save new reduced_datums of the appropriate data_type from the query results
        Be sure to use `ReducedDatum.objects.get_or_create()` when creating new objects.

        :param target: Target Object to be associated with the reduced data
        :param data: List of data dictionaries of the appropriate `data_type`
        :param data_type: An appropriate data type as listed in tom_dataproducts.models.DATA_TYPE_CHOICES
        :return: List of Reduced Datums (either retrieved or created)
        """
        reduced_datums = []
        for datum in data:
            if data_type == 'photometry':
                reduced_datum, __ = PhotometryReducedDatum.objects.get_or_create(
                    target=target,
                    source_name=self.name,
                    telescope=datum.get('telescope'),
                    instrument=datum.get('instrument'),
                    brightness=datum.get('brightness'),
                    limit=datum.get('limiting_brightness'),
                    brightness_error=datum.get('brightness_error'),
                    bandpass=datum.get('bandpass'),
                    unit=datum.get('brightness_unit') or datum.get('limiting_brightness_unit'),
                    exposure_time=datum.get('exposure_time'),
                )
                reduced_datums.append(reduced_datum)
        return reduced_datums

    def _fetch_full_message(self, message_uuid: str):
        """GET the full HERMES message body by uuid; return the JSON dict or an empty dict.
        """
        template = self.get_urls(url_type='message_url_template')
        if not template:
            logger.warning('_fetch_full_message: no message_url_template configured.')
            return {}
        message_url = template.format(uuid=message_uuid)
        try:
            response = requests.get(message_url, headers=self.build_headers(), timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.warning('Could not fetch HERMES message %s: %s', message_uuid, exc)
            return {}
