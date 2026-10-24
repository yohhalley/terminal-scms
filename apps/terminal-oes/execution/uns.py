from django.conf import settings


def site_root(site):
    return f"{settings.SITE_UNS_ROOT}/{site.enterprise.code}/{site.code}"


def execution_event_topic(site, order_id):
    return f"{site_root(site)}/oes/orders/{order_id}/execution"


def subscriptions():
    site_namespace = settings.SITE_UNS_ROOT
    return [
        (f"{site_namespace}/+/+/tos/orders/+/released", 1),
        (f"{site_namespace}/+/+/tos/site/created", 1),
        (f"{site_namespace}/+/+/tos/configuration/published", 1),
        ("spBv1.0/+/NBIRTH/+", 1),
        ("spBv1.0/+/NDEATH/+", 1),
        ("spBv1.0/+/NDATA/+", 1),
        ("spBv1.0/+/DBIRTH/+/+", 1),
        ("spBv1.0/+/DDEATH/+/+", 1),
        ("spBv1.0/+/DDATA/+/+", 1),
    ]
