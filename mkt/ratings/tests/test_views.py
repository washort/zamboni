# -*- coding: utf-8 -*-
from datetime import datetime
import json
from urlparse import urlparse

from django.core.urlresolvers import reverse
from django.db import reset_queries
from django.http import QueryDict
from django.test.utils import override_settings


import mock
from mock import patch
from nose.tools import eq_, ok_

import mkt
import mkt.regions
from mkt.api.tests.test_oauth import RestOAuth
from mkt.developers.models import ActivityLog
from mkt.prices.models import WebappPurchase
from mkt.ratings.models import Review, ReviewFlag
from mkt.site.fixtures import fixture
from mkt.site.utils import app_factory, version_factory
from mkt.webapps.models import WebappExcludedRegion, WebappUser, Webapp
from mkt.users.models import UserProfile


@mock.patch('mkt.webapps.models.Webapp.get_cached_manifest', mock.Mock)
class TestRatingResource(RestOAuth, mkt.site.tests.MktPaths):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestRatingResource, self).setUp()
        self.app = Webapp.objects.get(pk=337141)
        self.user = UserProfile.objects.get(pk=2519)
        self.user2 = UserProfile.objects.get(pk=31337)
        self.list_url = reverse('ratings-list')

    def _get_url(self, url, client=None, **kwargs):
        if client is None:
            client = self.client
        res = client.get(url, kwargs)
        data = json.loads(res.content)
        return res, data

    def _get_filter(self, client=None, expected_status=200, **params):
        res, data = self._get_url(self.list_url, client=client, **params)
        eq_(res.status_code, expected_status)
        if expected_status == 200:
            eq_(len(data['objects']), 1)
        return res, data

    def _compare_review_data(self, client, data, review):
        self.assertApiUrlEqual(data['app'], '/apps/app/337141/')
        eq_(data['body'], review.body)
        self.assertCloseToNow(data['created'], now=review.created)
        self.assertCloseToNow(data['modified'], now=review.modified)
        eq_(data['rating'], review.rating)
        eq_(data['report_spam'],
            reverse('ratings-flag', kwargs={'pk': review.pk}))
        eq_(data['resource_uri'],
            reverse('ratings-detail', kwargs={'pk': review.pk}))
        eq_(data['user']['display_name'], review.user.display_name)
        eq_(data['version']['version'], review.version.version)
        eq_(data['version']['resource_uri'],
            reverse('version-detail', kwargs={'pk': review.version.pk}))

        if client != self.anon:
            eq_(data['is_author'], review.user == self.user)
        else:
            ok_('is_author' not in data)

    def test_has_cors(self):
        self.assertCORS(self.client.get(self.list_url),
                        'get', 'post', 'put', 'delete')

    def test_options(self):
        res = self.anon.options(self.list_url)
        eq_(res.status_code, 200)
        data = json.loads(res.content)
        ok_('application/json' in data['renders'])
        ok_('application/json' in data['parses'])

    def test_get_empty_with_app(self):
        WebappUser.objects.create(user=self.user, webapp=self.app)
        res, data = self._get_url(self.list_url, app=self.app.pk)
        eq_(res.status_code, 200)
        eq_(data['info']['average'], self.app.average_rating)
        eq_(data['info']['slug'], self.app.app_slug)
        assert not data['user']['can_rate']
        assert not data['user']['has_rated']

    def test_get(self, client=None):
        first_version = self.app.current_version
        rev = Review.objects.create(webapp=self.app, user=self.user,
                                    version=first_version,
                                    body=u'I lôve this app',
                                    rating=5)
        rev.update(created=self.days_ago(2))
        rev2 = Review.objects.create(webapp=self.app, user=self.user2,
                                     version=first_version,
                                     body=u'I also lôve this app',
                                     rating=4)
        # Extra review for another app, should be ignored.
        extra_app = app_factory()
        Review.objects.create(webapp=extra_app, user=self.user,
                              version=extra_app.current_version,
                              body=u'I häte this extra app',
                              rating=1)

        self.app.total_reviews = 2
        ver = version_factory(webapp=self.app, version='2.0',
                              file_kw=dict(status=mkt.STATUS_PUBLIC))
        self.app.update_version()

        reset_queries()
        res, data = self._get_url(self.list_url, app=self.app.pk,
                                  client=client)
        eq_(len(data['objects']), 2)
        self._compare_review_data(client, data['objects'][0], rev2)
        self._compare_review_data(client, data['objects'][1], rev)
        eq_(data['info']['average'], self.app.average_rating)
        eq_(data['info']['slug'], self.app.app_slug)
        eq_(data['info']['current_version'], ver.version)
        if client != self.anon:
            eq_(data['user']['can_rate'], True)
            eq_(data['user']['has_rated'], True)
        return res

    def test_get_304(self):
        etag = self.test_get(client=self.anon)['ETag']
        res = self.anon.get(self.list_url, {'app': self.app.pk},
                            HTTP_IF_NONE_MATCH='%s' % etag)
        eq_(res.status_code, 304)

    @override_settings(DEBUG=True)
    def test_get_anonymous_queries(self):
        first_version = self.app.current_version
        Review.objects.create(webapp=self.app, user=self.user,
                              version=first_version,
                              body=u'I lôve this app',
                              rating=5)
        Review.objects.create(webapp=self.app, user=self.user2,
                              version=first_version,
                              body=u'I also lôve this app',
                              rating=4)
        self.app.total_reviews = 2
        version_factory(webapp=self.app, version='2.0',
                        file_kw=dict(status=mkt.STATUS_PUBLIC))
        self.app.update_version()

        reset_queries()
        with self.assertNumQueries(7):
            # 7 queries:
            # - 1 SAVEPOINT
            # - 2 for the Reviews queryset and the translations
            # - 2 for the Version associated to the reviews (qs + translations)
            # - 1 for the File attached to the Version
            # - 1 RELEASE SAVEPOINT
            #
            # Notes:
            # - In prod, we actually do COMMIT/ROLLBACK and not
            # SAVEPOINT/RELEASE SAVEPOINT. It would be nice to avoid those for
            # all GET requests in the API, but it's not trivial to do for
            # ViewSets which implement multiple actions through the same view
            # function (non_atomic_requests() really want to be applied to the
            # view function).
            #
            # - The query count is slightly higher in prod. In tests, we patch
            # get_app() to avoid the app queries to pollute the queries count.
            #
            # Once we are on django 1.7, we'll be able to play with Prefetch
            # to reduce the number of queries further by customizing the
            # queryset used for the complex related objects like versions and
            # webapp.
            with patch('mkt.ratings.views.RatingViewSet.get_app') as get_app:
                get_app.return_value = self.app
                res, data = self._get_url(self.list_url, client=self.anon,
                                          app=self.app.pk)

    def test_is_flagged_false(self):
        Review.objects.create(webapp=self.app, user=self.user2, body='yes',
                              rating=5)
        res, data = self._get_url(self.list_url, app=self.app.pk)
        eq_(data['objects'][0]['is_author'], False)
        eq_(data['objects'][0]['has_flagged'], False)

    def test_is_flagged_is_author(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        res, data = self._get_url(self.list_url, app=self.app.pk)
        eq_(data['objects'][0]['is_author'], True)
        eq_(data['objects'][0]['has_flagged'], False)

    def test_is_flagged_true(self):
        rat = Review.objects.create(webapp=self.app, user=self.user2,
                                    body='ah', rating=5)
        ReviewFlag.objects.create(review=rat, user=self.user,
                                  flag=ReviewFlag.SPAM)
        res, data = self._get_url(self.list_url, app=self.app.pk)
        eq_(data['objects'][0]['is_author'], False)
        eq_(data['objects'][0]['has_flagged'], True)

    def test_get_detail(self):
        fmt = '%Y-%m-%dT%H:%M:%S'
        Review.objects.create(webapp=self.app, user=self.user2, body='no',
                              rating=5)
        rev = Review.objects.create(webapp=self.app, user=self.user,
                                    body='yes', rating=5)
        url = reverse('ratings-detail', kwargs={'pk': rev.pk})
        res, data = self._get_url(url)
        self.assertCloseToNow(datetime.strptime(data['modified'], fmt))
        self.assertCloseToNow(datetime.strptime(data['created'], fmt))
        eq_(data['body'], 'yes')

    def test_filter_self(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        Review.objects.create(webapp=self.app, user=self.user2, body='no',
                              rating=5)
        self._get_filter(user=self.user.pk)

    def test_filter_mine(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        Review.objects.create(webapp=self.app, user=self.user2, body='no',
                              rating=5)
        self._get_filter(user='mine')

    def test_filter_mine_anonymous(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        self._get_filter(user='mine', client=self.anon, expected_status=403)

    def test_filter_by_app_slug(self):
        self.app2 = app_factory()
        Review.objects.create(webapp=self.app2, user=self.user, body='no',
                              rating=5)
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        res, data = self._get_filter(app=self.app.app_slug)
        eq_(data['info']['slug'], self.app.app_slug)
        eq_(data['info']['current_version'], self.app.current_version.version)

    def test_filter_by_app_pk(self):
        self.app2 = app_factory()
        Review.objects.create(webapp=self.app2, user=self.user, body='no',
                              rating=5)
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        res, data = self._get_filter(app=self.app.pk)
        eq_(data['info']['slug'], self.app.app_slug)
        eq_(data['info']['current_version'], self.app.current_version.version)

    def test_filter_by_invalid_app(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        self._get_filter(app='wrongslug', expected_status=404)
        self._get_filter(app=2465478, expected_status=404)

    def test_filter_by_nonpublic_app(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        self.app.update(status=mkt.STATUS_PENDING)
        res, data = self._get_filter(
            app=self.app.app_slug, expected_status=403)
        eq_(data['detail'], 'The app requested is not public')

    def test_filter_by_nonpublic_app_admin(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        self.grant_permission(self.user, 'Apps:Edit')
        self.app.update(status=mkt.STATUS_PENDING)
        self._get_filter(app=self.app.app_slug)

    def test_filter_by_nonpublic_app_owner(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        WebappUser.objects.create(user=self.user, webapp=self.app)
        self.app.update(status=mkt.STATUS_PENDING)
        self._get_filter(app=self.app.app_slug)

    def test_anonymous_get_list_without_app(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        res, data = self._get_url(self.list_url, client=self.anon)
        eq_(res.status_code, 200)
        assert 'user' not in data
        eq_(len(data['objects']), 1)
        eq_(data['objects'][0]['body'], 'yes')

    def test_anonymous_get_list_app(self):
        res, data = self._get_url(self.list_url, app=self.app.app_slug,
                                  client=self.anon)
        eq_(res.status_code, 200)
        eq_(data['user'], None)

    def test_non_owner(self):
        res, data = self._get_url(self.list_url, app=self.app.app_slug)
        assert data['user']['can_rate']
        assert not data['user']['has_rated']

    @patch('mkt.webapps.models.Webapp.get_excluded_region_ids')
    def test_can_rate_unpurchased(self, exclude_mock):
        exclude_mock.return_value = []
        self.app.update(premium_type=mkt.WEBAPP_PREMIUM)
        res, data = self._get_url(self.list_url, app=self.app.app_slug)
        assert not res.json['user']['can_rate']

    @patch('mkt.webapps.models.Webapp.get_excluded_region_ids')
    def test_can_rate_purchased(self, exclude_mock):
        exclude_mock.return_value = []
        self.app.update(premium_type=mkt.WEBAPP_PREMIUM)
        WebappPurchase.objects.create(webapp=self.app, user=self.user)
        res, data = self._get_url(self.list_url, app=self.app.app_slug)
        assert res.json['user']['can_rate']

    def test_isowner_true(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        res, data = self._get_url(self.list_url, app=self.app.app_slug)
        data = json.loads(res.content)
        eq_(data['objects'][0]['is_author'], True)

    def test_isowner_false(self):
        Review.objects.create(webapp=self.app, user=self.user2, body='yes',
                              rating=5)
        res, data = self._get_url(self.list_url, app=self.app.app_slug)
        data = json.loads(res.content)
        eq_(data['objects'][0]['is_author'], False)

    def test_isowner_anonymous(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        res, data = self._get_url(self.list_url, app=self.app.app_slug,
                                  client=self.anon)
        data = json.loads(res.content)
        self.assertNotIn('is_author', data['objects'][0])

    def test_already_rated(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=5)
        res, data = self._get_url(self.list_url, app=self.app.app_slug)
        data = json.loads(res.content)
        assert data['user']['can_rate']
        assert data['user']['has_rated']

    def test_already_rated_version(self):
        self.app.update(is_packaged=True)
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              rating=4)
        version_factory(webapp=self.app, version='3.0')
        self.app.update_version()
        res, data = self._get_url(self.list_url, app=self.app.app_slug)
        data = json.loads(res.content)
        assert data['user']['can_rate']
        assert not data['user']['has_rated']

    def test_no_lang_filter(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              lang='en', rating=5)
        other_user = UserProfile.objects.exclude(pk=self.user.pk)[0]
        Review.objects.create(webapp=self.app, user=other_user, body='yes',
                              lang='pt', rating=5)
        res, data = self._get_url(self.list_url, app=self.app.app_slug,
                                  client=self.anon, lang='pt')
        eq_(res.status_code, 200)
        eq_(len(data['objects']), 2)
        eq_(data['info']['total_reviews'], 2)

    def test_lang_filter(self):
        Review.objects.create(webapp=self.app, user=self.user, body='yes',
                              lang='en', rating=5)
        other_user = UserProfile.objects.exclude(pk=self.user.pk)[0]
        Review.objects.create(webapp=self.app, user=other_user, body='yes',
                              lang='pt', rating=5)
        res, data = self._get_url(self.list_url, app=self.app.app_slug,
                                  client=self.anon, lang='pt',
                                  match_lang='1')
        eq_(res.status_code, 200)
        eq_(len(data['objects']), 1)
        eq_(data['info']['total_reviews'], 2)
        eq_(data['objects'][0]['lang'], 'pt')

    def _create(self, data=None, anonymous=False, version=None):
        version = version or self.app.current_version
        default_data = {
            'app': self.app.id,
            'body': 'Rocking the free web.',
            'rating': 5,
            'version': version.id
        }
        if data:
            default_data.update(data)
        json_data = json.dumps(default_data)
        client = self.anon if anonymous else self.client
        res = client.post(self.list_url, data=json_data)
        try:
            res_data = json.loads(res.content)
        except ValueError:
            res_data = res.content
        return res, res_data

    def test_anonymous_create_fails(self):
        res, data = self._create(anonymous=True)
        eq_(res.status_code, 403)

    @patch('mkt.ratings.views.record_action')
    def test_create(self, record_action):
        log_review_id = mkt.LOG.ADD_REVIEW.id
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 0)
        res, data = self._create()
        eq_(201, res.status_code)
        pk = Review.objects.latest('pk').pk
        eq_(data['body'], 'Rocking the free web.')
        eq_(data['rating'], 5)
        eq_(data['resource_uri'], reverse('ratings-detail', kwargs={'pk': pk}))
        eq_(data['report_spam'], reverse('ratings-flag', kwargs={'pk': pk}))
        eq_(data['lang'], 'en')

        eq_(record_action.call_count, 1)
        eq_(record_action.call_args[0][0], 'new-review')
        eq_(record_action.call_args[0][2], {'app-id': 337141})
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 1)

        return res, data

    def test_create_fr(self):
        res, data = self._create(data={'body': 'Je peux manger du verre'})
        eq_(data['lang'], 'fr')

    def test_create_packaged(self):
        self.app.update(is_packaged=True)
        res, data = self.test_create()
        eq_(data['version']['version'], '1.0')

    def test_create_bad_data(self):
        res, data = self._create({'body': None})
        eq_(400, res.status_code)
        assert 'body' in data

    def test_create_bad_rating(self):
        res, data = self._create({'rating': 0})
        eq_(400, res.status_code)
        assert 'rating' in data

    def test_create_nonexistent_app(self):
        res, data = self._create({'app': -1})
        eq_(400, res.status_code)
        assert 'app' in data

    @patch('mkt.ratings.serializers.get_region')
    def test_create_for_nonregion(self, get_region_mock):
        WebappExcludedRegion.objects.create(webapp=self.app,
                                            region=mkt.regions.BRA.id)
        get_region_mock.return_value = mkt.regions.BRA
        res, data = self._create()
        eq_(403, res.status_code)

    def test_create_for_nonpublic(self):
        self.app.update(status=mkt.STATUS_PENDING)
        res, data = self._create(version=self.app.latest_version)
        eq_(403, res.status_code)

    def test_create_duplicate_rating(self):
        self._create()
        res, data = self._create()
        eq_(409, res.status_code)

    def test_new_rating_for_new_version(self):
        self.app.update(is_packaged=True)
        self._create()
        version = version_factory(webapp=self.app, version='3.0')
        self.app.update_version()
        eq_(self.app.reload().current_version, version)
        res, data = self._create()
        eq_(201, res.status_code)
        eq_(data['version']['version'], '3.0')

    def test_create_duplicate_rating_packaged(self):
        self.app.update(is_packaged=True)
        self._create()
        res, data = self._create()
        eq_(409, res.status_code)

    def test_create_own_app(self):
        WebappUser.objects.create(user=self.user, webapp=self.app)
        res, data = self._create()
        eq_(403, res.status_code)

    @patch('mkt.webapps.models.Webapp.get_excluded_region_ids')
    def test_rate_unpurchased_premium(self, exclude_mock):
        exclude_mock.return_value = []
        self.app.update(premium_type=mkt.WEBAPP_PREMIUM)
        res, data = self._create()
        eq_(403, res.status_code)

    @patch('mkt.webapps.models.Webapp.get_excluded_region_ids')
    def test_rate_purchased_premium(self, exclude_mock):
        exclude_mock.return_value = []
        self.app.update(premium_type=mkt.WEBAPP_PREMIUM)
        WebappPurchase.objects.create(webapp=self.app, user=self.user)
        res, data = self._create()
        eq_(201, res.status_code)

    def _create_default_review(self):
        # Create the original review
        default_data = {
            'body': 'Rocking the free web.',
            'rating': 5
        }
        res, res_data = self._create(default_data)
        return res, res_data

    def test_patch_not_implemented(self):
        self._create_default_review()
        pk = Review.objects.latest('id').pk
        json_data = json.dumps({
            'body': 'Totally rocking the free web.',
        })
        res = self.client.patch(reverse('ratings-detail', kwargs={'pk': pk}),
                                data=json_data)
        # Should return a 405 but permission check is done first. It's fine.
        eq_(res.status_code, 403)

    def _update(self, updated_data, pk=None):
        # Update the review
        if pk is None:
            pk = Review.objects.latest('id').pk
        json_data = json.dumps(updated_data)
        res = self.client.put(reverse('ratings-detail', kwargs={'pk': pk}),
                              data=json_data)
        try:
            res_data = json.loads(res.content)
        except ValueError:
            res_data = res.content
        return res, res_data

    def test_update(self):
        rev = Review.objects.create(webapp=self.app, user=self.user,
                                    body='abcd', ip_address='1.2.3.4',
                                    rating=5)
        new_data = {
            'body': 'Totally rocking the free web.',
            'rating': 4,
        }
        log_review_id = mkt.LOG.EDIT_REVIEW.id
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 0)
        res, data = self._update(new_data)
        eq_(res.status_code, 200)
        eq_(data['body'], new_data['body'])
        eq_(data['rating'], new_data['rating'])
        rev.reload()
        eq_(rev.body, new_data['body'])
        eq_(rev.rating, new_data['rating'])
        eq_(rev.user, self.user)
        eq_(rev.ip_address, '1.2.3.4')
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 1)

    def test_update_admin(self):
        self.grant_permission(self.user, 'Apps:Edit')
        rev = Review.objects.create(webapp=self.app, user=self.user2,
                                    body='abcd', ip_address='1.2.3.4',
                                    rating=5)
        new_data = {
            'body': 'Edited by admin',
            'rating': 1,
        }
        log_review_id = mkt.LOG.EDIT_REVIEW.id
        res = self.client.put(reverse('ratings-detail', kwargs={'pk': rev.pk}),
                              json.dumps(new_data))
        eq_(res.status_code, 200)
        data = json.loads(res.content)
        eq_(data['body'], new_data['body'])
        eq_(data['rating'], new_data['rating'])
        rev.reload()
        eq_(rev.body, new_data['body'])
        eq_(rev.rating, new_data['rating'])
        eq_(rev.user, self.user2)
        eq_(rev.ip_address, '1.2.3.4')
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 1)

    def test_update_bad_data(self):
        self._create_default_review()
        res, data = self._update({'body': None})
        eq_(400, res.status_code)
        assert 'body' in data

    def test_update_change_app(self):
        _, previous_data = self._create_default_review()
        self.app2 = app_factory()
        new_data = {
            'body': 'Totally rocking the free web.',
            'rating': 4,
            'app': self.app2.pk
        }
        res, data = self._update(new_data)
        eq_(res.status_code, 200)
        eq_(data['body'], new_data['body'])
        eq_(data['rating'], new_data['rating'])
        eq_(data['app'], previous_data['app'])

    def test_update_comment_not_mine(self):
        rev = Review.objects.create(webapp=self.app, user=self.user2,
                                    body='yes', rating=4)
        res = self.client.put(reverse('ratings-detail', kwargs={'pk': rev.pk}),
                              json.dumps({'body': 'no', 'rating': 1}))
        eq_(res.status_code, 403)
        rev.reload()
        eq_(rev.body, 'yes')

    def test_delete_app_mine(self):
        WebappUser.objects.filter(webapp=self.app).update(user=self.user)
        rev = Review.objects.create(webapp=self.app, user=self.user2,
                                    body='yes', rating=5)
        url = reverse('ratings-detail', kwargs={'pk': rev.pk})
        res = self.client.delete(url)
        eq_(res.status_code, 204)
        eq_(Review.objects.count(), 0)
        log_review_id = mkt.LOG.DELETE_REVIEW.id
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 1)

    def test_delete_comment_mine(self):
        rev = Review.objects.create(webapp=self.app, user=self.user,
                                    body='yes', rating=1)
        url = reverse('ratings-detail', kwargs={'pk': rev.pk})
        res = self.client.delete(url)
        eq_(res.status_code, 204)
        eq_(Review.objects.count(), 0)
        log_review_id = mkt.LOG.DELETE_REVIEW.id
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 1)

    def test_delete_webapps_admin(self):
        self.grant_permission(self.user, 'Apps:Edit')
        rev = Review.objects.create(webapp=self.app, user=self.user2,
                                    body='yes', rating=1)
        url = reverse('ratings-detail', kwargs={'pk': rev.pk})
        res = self.client.delete(url)
        eq_(res.status_code, 204)
        eq_(Review.objects.count(), 0)
        log_review_id = mkt.LOG.DELETE_REVIEW.id
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 1)

    def test_delete_users_admin(self):
        self.grant_permission(self.user, 'Users:Edit')
        rev = Review.objects.create(webapp=self.app, user=self.user2,
                                    body='yes', rating=5)
        url = reverse('ratings-detail', kwargs={'pk': rev.pk})
        res = self.client.delete(url)
        eq_(res.status_code, 204)
        eq_(Review.objects.count(), 0)
        log_review_id = mkt.LOG.DELETE_REVIEW.id
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 1)

    def test_delete_not_mine(self):
        rev = Review.objects.create(webapp=self.app, user=self.user2,
                                    body='yes', rating=3)
        url = reverse('ratings-detail', kwargs={'pk': rev.pk})
        self.app.authors.clear()
        res = self.client.delete(url)
        eq_(res.status_code, 403)
        eq_(Review.objects.count(), 1)
        log_review_id = mkt.LOG.DELETE_REVIEW.id
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 0)

    def test_delete_not_there(self):
        url = reverse('ratings-detail', kwargs={'pk': 123})
        res = self.client.delete(url)
        eq_(res.status_code, 404)
        log_review_id = mkt.LOG.DELETE_REVIEW.id
        eq_(ActivityLog.objects.filter(action=log_review_id).count(), 0)


class TestRatingResourcePagination(RestOAuth, mkt.site.tests.MktPaths):
    fixtures = fixture('user_2519', 'user_999', 'webapp_337141')

    def setUp(self):
        super(TestRatingResourcePagination, self).setUp()
        self.app = Webapp.objects.get(pk=337141)
        self.user = UserProfile.objects.get(pk=2519)
        self.user2 = UserProfile.objects.get(pk=31337)
        self.user3 = UserProfile.objects.get(pk=999)
        self.url = reverse('ratings-list')

    def test_pagination(self):
        first_version = self.app.current_version
        rev1 = Review.objects.create(webapp=self.app, user=self.user,
                                     version=first_version,
                                     body=u'I häte this app',
                                     rating=0)
        rev2 = Review.objects.create(webapp=self.app, user=self.user2,
                                     version=first_version,
                                     body=u'I lôve this app',
                                     rating=5)
        rev3 = Review.objects.create(webapp=self.app, user=self.user3,
                                     version=first_version,
                                     body=u'Blurp.',
                                     rating=3)
        rev1.update(created=self.days_ago(3))
        rev2.update(created=self.days_ago(2))
        self.app.update(total_reviews=3)
        res = self.client.get(self.url, {'app': self.app.pk, 'limit': 2})
        eq_(res.status_code, 200)
        data = json.loads(res.content)

        eq_(len(data['objects']), 2)
        eq_(data['objects'][0]['body'], rev3.body)
        eq_(data['objects'][1]['body'], rev2.body)
        eq_(data['meta']['total_count'], 3)
        eq_(data['meta']['limit'], 2)
        eq_(data['meta']['previous'], None)
        eq_(data['meta']['offset'], 0)
        next = urlparse(data['meta']['next'])
        eq_(next.path, self.url)
        eq_(QueryDict(next.query).dict(),
            {'app': str(self.app.pk), 'limit': '2', 'offset': '2'})

        res = self.client.get(self.url,
                              {'app': self.app.pk, 'limit': 2, 'offset': 2})
        eq_(res.status_code, 200)
        data = json.loads(res.content)

        eq_(len(data['objects']), 1)
        eq_(data['objects'][0]['body'], rev1.body)
        eq_(data['meta']['total_count'], 3)
        eq_(data['meta']['limit'], 2)
        prev = urlparse(data['meta']['previous'])
        eq_(next.path, self.url)
        eq_(QueryDict(prev.query).dict(),
            {'app': str(self.app.pk), 'limit': '2', 'offset': '0'})
        eq_(data['meta']['offset'], 2)
        eq_(data['meta']['next'], None)

    def test_total_count(self):
        Review.objects.create(webapp=self.app, user=self.user,
                              version=self.app.current_version,
                              body=u'I häte this app',
                              rating=0)
        self.app.update(total_reviews=42)
        res = self.client.get(self.url)
        data = json.loads(res.content)

        # We are not passing an app, so the app's total_reviews isn't used.
        eq_(data['meta']['total_count'], 1)

        # With an app however, it should be used as the total count.
        res = self.client.get(self.url, data={'app': self.app.pk})
        data = json.loads(res.content)
        eq_(data['meta']['total_count'], 42)

    def test_pagination_invalid(self):
        res = self.client.get(self.url, data={'offset': '%E2%98%83'})
        eq_(res.status_code, 200)


class TestReviewFlagResource(RestOAuth, mkt.site.tests.MktPaths):
    fixtures = fixture('user_2519', 'webapp_337141')

    def setUp(self):
        super(TestReviewFlagResource, self).setUp()
        self.app = Webapp.objects.get(pk=337141)
        self.user = UserProfile.objects.get(pk=2519)
        self.user2 = UserProfile.objects.get(pk=31337)
        self.rating = Review.objects.create(webapp=self.app, rating=5,
                                            user=self.user2, body='yes')
        self.flag_url = reverse('ratings-flag', kwargs={'pk': self.rating.pk})

    def test_has_cors(self):
        self.assertCORS(self.client.post(self.flag_url), 'post')

    def test_flag(self):
        data = json.dumps({'flag': ReviewFlag.SPAM})
        res = self.client.post(self.flag_url, data=data)
        eq_(res.status_code, 201)
        rf = ReviewFlag.objects.get(review=self.rating)
        eq_(rf.user, self.user)
        eq_(rf.flag, ReviewFlag.SPAM)
        eq_(rf.note, '')

    def test_flag_note(self):
        note = 'do not want'
        data = json.dumps({'flag': ReviewFlag.SPAM, 'note': note})
        res = self.client.post(self.flag_url, data=data)
        eq_(res.status_code, 201)
        rf = ReviewFlag.objects.get(review=self.rating)
        eq_(rf.user, self.user)
        eq_(rf.flag, ReviewFlag.OTHER)
        eq_(rf.note, note)

    def test_flag_anon(self):
        data = json.dumps({'flag': ReviewFlag.SPAM})
        res = self.anon.post(self.flag_url, data=data)
        eq_(res.status_code, 201)
        rf = ReviewFlag.objects.get(review=self.rating)
        eq_(rf.user, None)
        eq_(rf.flag, ReviewFlag.SPAM)
        eq_(rf.note, '')

    def test_flag_conflict(self):
        data = json.dumps({'flag': ReviewFlag.SPAM})
        res = self.client.post(self.flag_url, data=data)
        res = self.client.post(self.flag_url, data=data)
        eq_(res.status_code, 409)
