from dataclasses import dataclass
import logging
import os
import requests
from dateutil.parser import parse

from crispy_forms.layout import Column, Fieldset, Layout, Row

from django import forms
from django.apps import apps
from django.conf import settings

from tom_hermes import __version__

# TODO: these imports are for upstream alerting which needs to be re-considered
#from confluent_kafka import KafkaException
#from hop import Stream
#from hop.auth import Auth
#from tom_alerts.exceptions import AlertSubmissionException
#from tom_alerts.alerts import GenericUpstreamSubmissionForm

from tom_alerts.alerts import GenericBroker, GenericQueryForm
from tom_targets.models import Target

logger = logging.getLogger(__name__)
#logger.setLevel(logging.DEBUG)

HERMES_URL = settings.HERMES_API_URL if hasattr(settings, 'HERMES_API_URL') else os.getenv('HERMES_BASE_URL', 'https://hermes.lco.global')
HERMES_API_VERSION = 0
HERMES_API_URL = f'{HERMES_URL}/api/v{HERMES_API_VERSION}'


class HermesQueryForm(GenericQueryForm):
    published_after = forms.CharField(required=False, label='Published after',
        widget=forms.TextInput(attrs={'type': 'date'})
    )
    published_before = forms.CharField(required=False, label='Published before',
        widget=forms.TextInput(attrs={'type': 'date'})
    )

    event_id = forms.CharField(required=False, label='Hermes Event ID')


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper.layout = Layout(
            self.common_layout,
            Fieldset(
                'Time Filters',
                Row(
                    Column('published_after'),
                    Column('published_before')
                )
            ),
            Fieldset(
                'Event Filters',
                'event_id',
            ),
        )

    def clean_published_after(self):
        published_after = self.cleaned_data['published_after']

        # this field is optional, so only parse it if there's a value to parse
        clean_published_after = ''
        if published_after:
            clean_published_after = parse(published_after).isoformat()

        return clean_published_after

    def clean_published_before(self):
        published_before = self.cleaned_data['published_before']

        # this field is optional, so only parse it if there's a value to parse
        clean_published_before = ''
        if published_before:
            clean_published_before = parse(published_before).isoformat()

        return clean_published_before

    def clean(self):
        cleaned_data = super().clean()
        # TODO: do we need to extend this method from the super?

        return cleaned_data


# TODO: Sort out "upstream" submission to Hopskotch functionality
#class SCIMMAUpstreamSubmissionForm(GenericUpstreamSubmissionForm):
#    topic = forms.CharField(required=False, max_length=100, widget=forms.HiddenInput())
#

@dataclass
class HermesNonLocalizedEventAlert:
    """Represent a Hermes NonlocalizedEvent for display as query result.

    HermesBroker._to_generic_alert does the translation
    """
    id: int # used for cache identification and retrieval 
    event_id: str
    nonlocalizedevent_id: int # PK of tom_nonlocalizedevent.model.NonLocalizedEvent (if exists)
    sequence_type: str
    sequence_number: int
    published: str
    hermes_url: str
    gracedb_url: str
    far: float # False Alarm Rate


class HermesBroker(GenericBroker):
    """
    This is a prototype interface to the Hermes Alert API 
    """

    name = 'Hermes'
    form = HermesQueryForm
    score_description = "False Alarm Rate (FAR) from Nonlocalized Event message. (1.0 for retractions)."
    # TODO: Sort out "upstream" submission to Hopskotch functionality
    #alert_submission_form = SCIMMAUpstreamSubmissionForm

    # the tom_alerts/views.py::RunQueryView.get_template_names() method will ask
    # this broker for the template to use for it's query results. Specify that here.
    template_name = 'tom_hermes/query_result.html'

    def get_broker_context_data(self, alerts):
        """Provide Broker-specific data to context for RunQueryView's template

        This method is called by RunQueryView.get_context_data with the alerts from
        the broker.fetch_alerts method below. (The `alerts` passed in here is the iterable
        returned from fetch_alerts).
        """
        # Report whether `tom_nonlocalizedevents` is installed.

        broker_context_for_view = {
            'tom_nonlocalizedevents_installed': apps.is_installed('tom_nonlocalizedevents'),
            'version': __version__, # from tom_hermes/__init__.py
        }

        return broker_context_for_view

    def _request_messages(self, parameters):
        logger.debug(f'HermesBroker._request_messages()  parameters: {parameters}')
        response = requests.get(f'{HERMES_API_URL}/nonlocalizedevents/',
                                params={**parameters})
        logger.debug(f'HermesBroker._request_messages() response.url: {response.url}')
        response.raise_for_status()
        return response.json()

    def fetch_alerts(self, parameters):
        logger.debug(f'HermesBroker.fetch_alerts()  parameters: {parameters}')
        parameters['page_size'] = 20
        result = self._request_messages(parameters)
        return iter(result['results'])

    def fetch_message(self, message_id):
        logger.debug(f'HermesBroker.fetch_message()  message_id: {message_id}')
        url = f'{HERMES_API_URL}/messages/{message_id}'
        response = requests.get(url)
        response.raise_for_status()
        json_data = response.json()
        return json_data

    def to_generic_alert(self, nonlocalizedevent):
        """Map the Hermes Nonlocalized fields into GenericAlert fields.
        Rather than tom_alerts.GenericAlert, tom_hermes uses the HermesNonLocalizedEventAlert
        dataclass to pass on to the query_result template.

        Use the last messages in the event sequence.
        """

        # Gather the GenericAlert properties from the nonlocalizedevent
        #  according to API V0 response
        event_id = nonlocalizedevent['event_id']
        url = f'{HERMES_URL}/nonlocalizedevent/{event_id}'
        # Use the last message in the sequence to get values
        message = nonlocalizedevent['sequences'][-1]['message'] # index -1 gives last in sequence
        sequence_type = nonlocalizedevent['sequences'][-1]['sequence_type']
        sequence_number = nonlocalizedevent['sequences'][-1]['sequence_number']
        try:
            gracedb_url = message['data'].get('urls', {}).get('gracedb', '')
        except KeyError:
            # a RETRACTION does not have an eventpage_url so use the previous alert in the sequence
            gracedb_url = nonlocalizedevent['sequences'][-2]['message']['data'].get('urls', {}).get('gracedb', '')

        # set score for this event as it's False Alarm Rate (far)
        # Retractions won't have FAR, so provide default
        if message['data'].get('event', {}):
            far = float(message['data'].get('event', {}).get('far', 1))
        else:
            far = 1

        published = parse(message['published'])

        # if tom_nonlocalizedevents is installed, then check if there is a
        # tom_nonlocalizedevents.models.NonLocalizedEvent instance for this event_id.
        # if so, return it's PK (for linking) as the id of the alert.
        nle_id = None # if event is not found
        if apps.is_installed('tom_nonlocalizedevents'):
            # yes, it's unorthodox to import here
            from tom_nonlocalizedevents.models import NonLocalizedEvent
            nonlocalized_event = NonLocalizedEvent.objects.filter(event_id=event_id).first()
            if nonlocalized_event is not None:
                nle_id = nonlocalized_event.id
        
        hermes_alert = HermesNonLocalizedEventAlert(
            id=event_id,
            event_id=event_id,
            nonlocalizedevent_id=nle_id,
            published=published.strftime('%d/%m/%Y %H:%M:%S'),
            sequence_type=sequence_type,
            sequence_number=sequence_number,
            hermes_url=url,
            gracedb_url=gracedb_url,
            far = far)

        return hermes_alert

    @DeprecationWarning
    def to_target(self, alert):
        logger.debug(f'HermesBroker.to_target()  message: {alert}')
        # Galactic Coordinates come in the format:
        # "gal_coords": "76.19,  5.74 [deg] galactic lon,lat of the counterpart",
        gal_coords = [None, None]
        # TODO: re-consider how these topics are hard-coded
        if alert['topic'] == 'lvc-counterpart':
            gal_coords = alert['message'].get('gal_coords', '').split('[')[0].split(',')
            gal_coords = [float(coord.strip()) for coord in gal_coords]

        target = Target.objects.create(
            name=alert['alert_identifier'],
            type='SIDEREAL',
            ra=alert['right_ascension'],
            dec=alert['declination'],
            galactic_lng=gal_coords[0],
            galactic_lat=gal_coords[1],
        )

        return target


# TODO: submit_upstream_alert needs to be reconsidered from the ground up to use HERMES/TreasureMap
#
#    def submit_upstream_alert(self, target=None, observation_record=None, **kwargs):
#        """
#        Submits target and observation record as Hopskotch alerts.
#
#        :param target: ``Target`` object to be converted to an alert and submitted upstream
#        :type target: ``Target``
#
#        :param observation_record: ``ObservationRecord`` object to be converted to an alert and submitted upstream
#        :type observation_record: ``ObservationRecord``
#
#        :param \\**kwargs:
#            See below
#
#        :Keyword Arguments:
#            * *topic* (``str``): Hopskotch topic to submit the alert to.
#
#        :returns: True or False depending on success of message submission
#        :rtype: bool
#
#        :raises:
#            AlertSubmissionException: If topic is not provided to the function and a default is not provided in
#                                      settings
#        """
#        creds = settings.BROKERS['SCIMMA']
#        stream = Stream(auth=Auth(creds['hopskotch_username'], creds['hopskotch_password']))
#        stream_url = creds['hopskotch_url']
#        topic = kwargs.get('topic') if kwargs.get('topic') else creds['default_hopskotch_topic']
#
#        if not topic:
#            raise AlertSubmissionException(f'Topic must be provided to submit alert to {self.name}')
#
#        try:
#            with stream.open(f'kafka://{stream_url}:9092/{topic}', 'w') as s:
#                if target:
#                    message = {'type': 'target', 'target_name': target.name, 'ra': target.ra, 'dec': target.dec}
#                    s.write(message)
#                if observation_record:
#                    message = {'type': 'observation', 'status': observation_record.status,
#                               'parameters': observation_record.parameters,
#                               'target_name': observation_record.target.name,
#                               'ra': observation_record.target.ra, 'dec': observation_record.target.dec,
#                               'facility': observation_record.facility}
#                    s.write(message)
#        except KafkaException as e:
#            raise AlertSubmissionException(f'Submission to Hopskotch failed: {e}')
#
#        return True
#