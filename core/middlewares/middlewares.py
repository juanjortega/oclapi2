import logging
import time

from request_logging.middleware import LoggingMiddleware
from core.common.constants import VERSION_HEADER, REQUEST_USER_HEADER, RESPONSE_TIME_HEADER, REQUEST_URL_HEADER, \
    REQUEST_METHOD_HEADER
from core.common.utils import set_current_user, set_request_url

request_logger = logging.getLogger('request_logger')
MAX_BODY_LENGTH = 50000


class BaseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response


class CustomLoggerMiddleware(LoggingMiddleware):
    def __call__(self, request):
        if request.META.get('HTTP_USER_AGENT', '').startswith('ELB-HealthChecker'):
            return self.get_response(request)
        return super().__call__(request)


class FixMalformedLimitParamMiddleware(BaseMiddleware):
    """
    Why this was necessary: https://github.com/OpenConceptLab/ocl_issues/issues/151
    """
    def __call__(self, request):
        to_remove = '?limit=100'
        if request.get_full_path()[-10:] == to_remove and request.method == 'GET':
            query_dict_copy = request.GET.copy()
            for key, value in query_dict_copy.copy().items():
                query_dict_copy[key] = value.replace(to_remove, '')
            if 'limit' not in query_dict_copy:
                query_dict_copy['limit'] = 100
                request.GET = query_dict_copy

        return self.get_response(request)


class ResponseHeadersMiddleware(BaseMiddleware):
    def __call__(self, request):
        start_time = time.time()
        response = self.get_response(request)
        from django.conf import settings
        response[VERSION_HEADER] = settings.VERSION
        response[REQUEST_USER_HEADER] = str(getattr(request, 'user', None))
        response[RESPONSE_TIME_HEADER] = time.time() - start_time
        response[REQUEST_URL_HEADER] = request.path
        response[REQUEST_METHOD_HEADER] = request.method
        return response


class CurrentUserMiddleware(BaseMiddleware):
    def __call__(self, request):
        set_current_user(lambda self: getattr(request, 'user', None))
        set_request_url(lambda self: request.build_absolute_uri())
        response = self.get_response(request)
        return response
