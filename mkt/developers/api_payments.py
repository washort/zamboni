from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse

import commonware
from rest_framework import status
from rest_framework.mixins import (CreateModelMixin, DestroyModelMixin,
                                   ListModelMixin, RetrieveModelMixin,
                                   UpdateModelMixin)
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.relations import (HyperlinkedIdentityField,
                                      HyperlinkedRelatedField)
from rest_framework.response import Response
from rest_framework.serializers import (HyperlinkedModelSerializer,
                                        Serializer,
                                        ValidationError)
from rest_framework.viewsets import GenericViewSet
from slumber.exceptions import HttpClientError, HttpServerError
from django.utils.translation import ugettext as _

import mkt
from lib.pay_server import get_client
from mkt.api.authentication import (RestOAuthAuthentication,
                                    RestSharedSecretAuthentication)
from mkt.api.base import MarketplaceView
from mkt.api.permissions import AllowAppOwner, GroupPermission
from mkt.constants.payments import PAYMENT_STATUSES
from mkt.constants.payments import PROVIDER_BANGO
from mkt.developers.forms_payments import (BangoPaymentAccountForm,
                                           PaymentCheckForm)
from mkt.developers.models import (AddonPaymentAccount, CantCancel,
                                   PaymentAccount)
from mkt.developers.providers import get_provider
from mkt.webapps.models import AddonUpsell, Webapp


log = commonware.log.getLogger('z.api.payments')


class PaymentAppViewSet(GenericViewSet):

    def initialize_request(self, request, *args, **kwargs):
        """
        Pass the value in the URL through to the form defined on the
        ViewSet, which will populate the app property with the app object.

        You must define a form which will take an app object.
        """
        request = (super(PaymentAppViewSet, self)
                   .initialize_request(request, *args, **kwargs))
        self.app = None
        form = self.form({'app': kwargs.get('pk')})
        if form.is_valid():
            self.app = form.cleaned_data['app']
        return request


class PaymentAccountSerializer(Serializer):
    """
    Fake serializer that returns PaymentAccount details when
    serializing a PaymentAccount instance. Use only for read operations.
    """

    def to_representation(self, obj):
        data = obj.get_provider().account_retrieve(obj)
        data['resource_uri'] = reverse('payment-account-detail',
                                       kwargs={'pk': obj.pk})
        return data


class PaymentAccountViewSet(ListModelMixin, RetrieveModelMixin,
                            MarketplaceView, GenericViewSet):
    queryset = PaymentAccount.objects.all()
    # PaymentAccountSerializer is not a real serializer, it just looks up
    # the details on the object. It's only used for GET requests, in every
    # other case we use BangoPaymentAccountForm directly.
    serializer_class = PaymentAccountSerializer
    authentication_classes = [RestOAuthAuthentication,
                              RestSharedSecretAuthentication]
    # Security checks are performed in get_queryset(), so we allow any
    # authenticated users by default.
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Return the queryset specific to the user using the view. (This replaces
        permission checks, unauthorized users won't be able to see that an
        account they don't have access to exists, we'll return 404 for them.)
        """
        qs = super(PaymentAccountViewSet, self).get_queryset()
        return qs.filter(user=self.request.user, inactive=False)

    def create(self, request, *args, **kwargs):
        provider = get_provider()
        form = provider.forms['account'](request.data)
        if form.is_valid():
            try:
                provider = get_provider()
                obj = provider.account_create(request.user, form.data)
            except HttpClientError as e:
                log.error('Client error creating Bango account; %s' % e)
                return Response(e.content,
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            except HttpServerError as e:
                log.error('Error creating Bango payment account; %s' % e)
                return Response(_(u'Could not connect to payment server.'),
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            serializer = self.get_serializer(obj)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = BangoPaymentAccountForm(request.data, account=True)
        if form.is_valid():
            self.object.get_provider().account_update(self.object,
                                                      form.cleaned_data)
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        account = self.get_object()
        try:
            account.cancel(disable_refs=True)
        except CantCancel:
            return Response(_('Cannot delete shared account'),
                            status=status.HTTP_409_CONFLICT)
        log.info('Account cancelled: %s' % account.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UpsellSerializer(HyperlinkedModelSerializer):
    free = HyperlinkedRelatedField(view_name='app-detail',
                                   queryset=Webapp.objects)
    premium = HyperlinkedRelatedField(view_name='app-detail',
                                      queryset=Webapp.objects)
    url = HyperlinkedIdentityField(view_name='app-upsell-detail')

    class Meta:
        model = AddonUpsell
        fields = ('free', 'premium', 'created', 'modified', 'url')
        view_name = 'app-upsell-detail'

    def validate(self, attrs):
        if ('free' not in attrs or
                attrs['free'].premium_type not in mkt.ADDON_FREES):
            raise ValidationError('Upsell must be from a free app.')

        if ('premium' not in attrs or
                attrs['premium'].premium_type in mkt.ADDON_FREES):
            raise ValidationError('Upsell must be to a premium app.')

        return attrs


class UpsellPermission(BasePermission):
    """
    Permissions on the upsell object, is determined by permissions on the
    free and premium object.
    """

    def check(self, request, free, premium):
        allow = AllowAppOwner()
        for app in free, premium:
            if app and not allow.has_object_permission(request, '', app):
                return False
        return True

    def has_object_permission(self, request, view, object):
        return self.check(request, object.free, object.premium)


class UpsellViewSet(CreateModelMixin, DestroyModelMixin, RetrieveModelMixin,
                    UpdateModelMixin, MarketplaceView, GenericViewSet):
    permission_classes = (UpsellPermission,)
    queryset = AddonUpsell.objects.filter()
    serializer_class = UpsellSerializer

    def perform_create(self, serializer):
        if not UpsellPermission().check(self.request,
                                        serializer.validated_data['free'],
                                        serializer.validated_data['premium']):
            raise PermissionDenied('Not allowed to alter that object')
        serializer.save()

    def perform_update(self, serializer):
        self.perform_create(serializer)


class AddonPaymentAccountPermission(BasePermission):
    """
    Permissions on the app payment account object, is determined by permissions
    on the app the account is being used for.
    """

    def check(self, request, app, account):
        if AllowAppOwner().has_object_permission(request, '', app):
            if account.shared or account.user.pk == request.user.pk:
                return True
            else:
                log.info('AddonPaymentAccount access %(account)s denied '
                         'for %(user)s: wrong user, not shared.'.format(
                             {'account': account.pk, 'user': request.user.pk}))
        else:
            log.info('AddonPaymentAccount access %(account)s denied '
                     'for %(user)s: no app permission.'.format(
                         {'account': account.pk, 'user': request.user.pk}))
        return False

    def has_object_permission(self, request, view, object):
        return self.check(request, object.addon, object.payment_account)


class AddonPaymentAccountSerializer(HyperlinkedModelSerializer):
    addon = HyperlinkedRelatedField(view_name='app-detail',
                                    queryset=Webapp.objects)
    payment_account = HyperlinkedRelatedField(
        view_name='payment-account-detail',
        queryset=PaymentAccount.objects)
    url = HyperlinkedIdentityField(view_name='app-payment-account-detail')

    class Meta:
        model = AddonPaymentAccount
        fields = ('addon', 'payment_account', 'created', 'modified', 'url')
        view_name = 'app-payment-account-detail'

    def validate(self, attrs):
        if attrs['addon'].premium_type in mkt.ADDON_FREES:
            raise ValidationError('App must be a premium app.')

        return attrs


class AddonPaymentAccountViewSet(CreateModelMixin, RetrieveModelMixin,
                                 UpdateModelMixin, MarketplaceView,
                                 GenericViewSet):
    permission_classes = (AddonPaymentAccountPermission,)
    queryset = AddonPaymentAccount.objects.all()
    serializer_class = AddonPaymentAccountSerializer

    def perform_create(self, serializer, created=True):
        if not AddonPaymentAccountPermission().check(
                self.request,
                serializer.validated_data['addon'],
                serializer.validated_data['payment_account']):
            raise PermissionDenied('Not allowed to alter that object.')

        if self.request.method != 'POST':
            if not self.queryset.filter(
                    addon=serializer.validated_data['addon'],
                    payment_account=serializer.validated_data[
                        'payment_account']).exists():
                # This should be a 400 error.
                raise PermissionDenied('Cannot change the add-on.')

        obj = serializer.save()

        if created:
            provider = get_provider()
            uri = provider.product_create(obj.payment_account, obj.addon)
            obj.product_uri = uri
        obj.save()

    def perform_update(self, obj):
        self.perform_create(obj, created=False)


class PaymentCheckViewSet(PaymentAppViewSet):
    permission_classes = (AllowAppOwner,)
    form = PaymentCheckForm

    def create(self, request, *args, **kwargs):
        """
        We aren't actually creating objects, but proxying them
        through to solitude.
        """
        if not self.app:
            return Response('', status=400)

        self.check_object_permissions(request, self.app)
        client = get_client()

        res = client.api.bango.status.post(
            data={'seller_product_bango':
                  self.app.payment_account(PROVIDER_BANGO).account_uri})

        filtered = {
            'bango': {
                'status': PAYMENT_STATUSES[res['status']],
                'errors': ''
            },
        }
        return Response(filtered, status=200)


class PaymentDebugViewSet(PaymentAppViewSet):
    permission_classes = [GroupPermission('Transaction', 'Debug')]
    form = PaymentCheckForm

    def list(self, request, *args, **kwargs):
        if not self.app:
            return Response('', status=400)

        client = get_client()
        res = client.api.bango.debug.get(
            data={'seller_product_bango':
                  self.app.payment_account(PROVIDER_BANGO).account_uri})
        filtered = {
            'bango': res['bango'],
        }
        return Response(filtered, status=200)
