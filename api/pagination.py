from rest_framework.pagination import LimitOffsetPagination



class FkDefaultPagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 1000