.. _site:

====
Site
====

Configuration about the site.


.. _categories:

Categories
==========

.. note:: The URL for this API will be moving.

.. http:get:: /api/v2/apps/category/

    Returns a list of categories available on the marketplace.

    **Request**

    Standard :ref:`list-query-params-label`.

    **Response**

    :param meta: :ref:`meta-response-label`.
    :type meta: object
    :param objects: A :ref:`listing <objects-response-label>` of :ref:`categories <category-response-label>`.
    :type objects: array
    :status 200: successfully completed.


.. _category-response-label:

.. http:get:: /api/v2/apps/category/(int:id)/

    Returns a category.

    **Response**

    .. code-block:: json

        {
            "id": 1,
            "name": "Games",
            "resource_uri": "/api/v2/apps/category/1/",
            "slug": "games"
        }


.. _carriers:

Carriers
========

.. http:get:: /api/v2/services/carrier/

    Returns a list of possible carriers for apps.

    **Response**


    :param meta: :ref:`meta-response-label`.
    :type meta: object
    :param objects: A :ref:`listing <objects-response-label>` of :ref:`carriers <carrier-response-label>`.
    :type objects: array
    :status 200: successfully completed.

.. _carrier-response-label:

.. http:get:: /api/v2/services/carrier/<slug>/

    Returns a carrier.

    **Request**

    Standard :ref:`list-query-params-label`.

    **Response**

    .. code-block:: json

        {
            "id": "1",
            "name": "PhoneORama",
            "resource_uri": "/api/v2/services/carrier/phoneorama/",
            "slug": "phoneorama"
        }

.. _regions:

Regions
=======

.. http:get:: /api/v2/services/region/

    Returns a list of possible regions for apps.

    **Response**


    :param meta: :ref:`meta-response-label`.
    :type meta: object
    :param objects: A :ref:`listing <objects-response-label>` of :ref:`regions <region-response-label>`.
    :type objects: array
    :status 200: successfully completed.

.. _region-response-label:

.. http:get:: /api/v2/services/region/<slug>/

    Returns a region.

    **Request**

    Standard :ref:`list-query-params-label`.

    **Response**

    .. code-block:: json

        {
            "id": "1",
            "name": "Appistan",
            "resource_uri": "/api/v2/services/region/ap/",
            "slug": "ap",
            "default_currency": "USD",
            "default_language": "en-AP",
        }

Configuration
=============

.. http:get:: /api/v2/services/config/site/

    Returns information about how the marketplace is configured. Not all
    settings and configuration options are returned - only a subset. This
    subset will change as features in the site change. The list of results
    should not be relied upon to stay consistent.

    **Response**

    :param fxa: an object containing Firefox Accounts auth information if the
                Firefox Accounts waffle switch is active.
    :type fxa: object
    :param version: the git commit number of the deployment.
    :type version: string|null
    :param settings: a subset of useful site settings.
    :type settings: object
    :param waffle: an object containing a list waffle flags and switches.
    :type waffle: object

    Example:

    .. code-block:: json

        {
            "fxa": {
                "fxa_auth_state": "aaabbbccctoken",
                "fxa_auth_url": "https://somelongauthurl.com?stuff=stuff"
            },
            "waffle": {
                "switches": {
                    "some-switch": {
                        "name": "some-switch",
                        "created": "2013-12-17T15:38:10",
                        "modified": "2013-12-17T15:38:10",
                        "note": "",
                        "active": true,
                        "id": 17
                    }
                },
                "flags": {
                    "some-flag": {
                        "note": "",
                        "users": [],
                        "testing": false,
                        "everyone": true,
                        "groups": [],
                        "rollout": false,
                        "id": 1,
                        "staff": false,
                        "superusers": true,
                        "authenticated": false,
                        "name": "a-flag",
                        "created": "2013-12-19T10:21:56",
                        "percent": null,
                        "modified": "2013-12-19T10:21:56",
                        "languages": ""
                   }
                }
            }
            "settings": {
                "SITE_URL": "http://z.mozilla.dev"
            },
            "version": null
        }

    **?serializer=commonplace**

    If you pass *commonplace* as a GET parameter for *serializer*, the switches
    response will be simply a list of names of the active switches.

    :param switches: a list of waffle switches
    :type switches: array

    Example:

    .. code-block:: json

        {
            ...

            "waffle": {
                "switches": ["user-curation", "feed"]
            },

            ...
        }

    For full information about waffle flags and switches, please see the waffle
    documentation: http://waffle.readthedocs.org/en/latest/types.html


Price tiers
===========

.. http:get:: /api/v2/services/price-tier/

    Lists price tiers.

    **Response**

    :param objects: A listing of :ref:`tiers <tier-response-label>`.


.. _tier-response-label:

.. http:get:: /api/v2/services/price-tier/(int:id)/

    Returns a price tier.

    **Response**

    :param resource_uri: The URI for this tier.
    :type resource_uri: string
    :param active: Whether the price tier is active.
    :type active: boolean
    :param name: The price tier name.
    :type name: string
    :param method: How payment may be submitted.
    :type method: string; one of "operator", "card", or "operator+card".


.. http:post:: /api/v2/services/price-tier/

    Create a price tier.

    .. note:: Requires admin account.

    **Request**

    :param active: Whether the price tier is active.
    :type active: boolean
    :param name: The price tier name.
    :type name: string
    :param method: How payment may be submitted.
    :type method: string; one of "operator", "card", or "operator+card".
    :param price: Price in US dollars.
    :type price: decimal string


.. http:put:: /api/v2/services/price-tier/(int:id)/

    Update a price tier.

    .. note:: Requires admin account.

    **Request**

    :param active: Whether the price tier is active.
    :type active: boolean
    :param name: The price tier name.
    :type name: string
    :param method: How payment may be submitted.
    :type method: string; one of "operator", "card", or "operator+card".
    :param price: Price in US dollars.
    :type price: decimal string


.. http:delete:: /api/v2/services/price-tier/(int:id)/

    Delete a price tier and all associated prices.

    .. note:: Requires admin account.


.. http:get:: /api/v2/services/price-currency/

   Lists prices in various currencies.

   **Request**

   :param tier: Price tier ID to select currencies for.
   :type tier: number

   **Response**

   :param objects: A listing of :ref:`prices <price-response-label>`.


.. _price-response-label:

.. http:get:: /api/v2/services/price-currency/(int:id)/

    Fetch a single price.

    **Response**

    :param id: Identifier for this price.
    :type id: number
    :param tier: ID of tier this price belongs to.
    :type tier: number
    :param currency: Code for this price's currency.
    :type currency: string
    :param carrier: Slug of carrier this price applies to.
    :type carrier: string
    :param price: Price in this currency.
    :type price: number
    :param provider: Name of payment provider for this price.
    :type provider: string
    :param method: How payment may be submitted.
    :type method: string; one of "operator", "card", or "operator+card".


.. http:post:: /api/v2/services/price-currency/

    Create a price.

    .. note:: Requires admin account.

    **Request**

    :param tier: ID of tier this price belongs to.
    :type tier: number
    :param currency: Code for this price's currency.
    :type currency: string
    :param carrier: Slug of carrier this price applies to.
    :type carrier: string
    :param price: Price in this currency.
    :type price: number
    :param provider: Name of payment provider for this price.
    :type provider: string
    :param method: How payment may be submitted.
    :type method: string; one of "operator", "card", or "operator+card".


.. http:put:: /api/v2/services/price-currency/(int:id)/

    Update a price.

    .. note:: requires an admin account.

    **Request**

    :param tier: ID of tier this price belongs to.
    :type tier: number
    :param currency: Code for this price's currency.
    :type currency: string
    :param carrier: Slug of carrier this price applies to.
    :type carrier: string
    :param price: Price in this currency.
    :type price: number
    :param provider: Name of payment provider for this price.
    :type provider: string
    :param method: How payment may be submitted.
    :type method: string; one of "operator", "card", or "operator+card".


.. http:delete:: /api/v2/services/price-currency/(int:id)/

    Delete a price.

    .. note:: Requires admin account.
