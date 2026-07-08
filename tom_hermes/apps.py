from django.apps import AppConfig
from django.urls import include, path


class TomHermesConfig(AppConfig):
    """AppConfig for the ``tom_hermes``.
    """
    default = True
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tom_hermes'

    def data_services(self):
        """Advertise ``HermesDataService`` to the tom_dataservices discovery.
        """
        return [{'class': f'{self.name}.dataservices.hermes.HermesDataService'}]

    def profile_details(self):
        """Register the user-profile page partial showing HERMES credential status.
        """
        return [{
            'partial': 'tom_hermes/partials/hermes_user_profile.html',
            'context': f'{self.name}.templatetags.hermes_extras.hermes_profile_data',
        }]

    def include_url_paths(self):
        """URL patterns this app contributes to the project URLconf.
        """
        return [path('hermes/', include(f'{self.name}.urls'))]

    def target_detail_buttons(self):
        """Integration point for adding buttons to the target detail view.
        """
        return [{'partial': f'{self.name}/partials/hermes_share_button.html',
                 'context': f'{self.name}.templatetags.hermes_extras.share_button'}]
