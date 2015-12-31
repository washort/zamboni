from contextlib import contextmanager

from nose.tools import eq_
from django.utils.translation import activate

import mkt.site.tests

import mkt.constants.ratingsbodies as ratingsbodies


class TestRatingsBodies(mkt.site.tests.TestCase):

    def test_all_ratings(self):
        ratings = ratingsbodies.ALL_RATINGS()

        # Assert all ratings bodies are present.
        assert ratingsbodies.CLASSIND_L in ratings
        assert ratingsbodies.GENERIC_3 in ratings
        assert ratingsbodies.ESRB_E in ratings
        assert ratingsbodies.PEGI_3 in ratings
        assert ratingsbodies.USK_0 in ratings

    def test_ratings_by_name_lazy_translation(self):
        generic_3_choice = ratingsbodies.RATINGS_BY_NAME()[6]
        eq_(generic_3_choice[1], 'Generic - For ages 3+')

    def test_ratings_has_ratingsbody(self):
        eq_(ratingsbodies.GENERIC_3.ratingsbody, ratingsbodies.GENERIC)
        eq_(ratingsbodies.CLASSIND_L.ratingsbody, ratingsbodies.CLASSIND)
        eq_(ratingsbodies.ESRB_E.ratingsbody, ratingsbodies.ESRB)
        eq_(ratingsbodies.USK_0.ratingsbody, ratingsbodies.USK)
        eq_(ratingsbodies.PEGI_3.ratingsbody, ratingsbodies.PEGI)

    def test_dehydrate_rating(self):

        for rating in ratingsbodies.ALL_RATINGS():
            rating = ratingsbodies.dehydrate_rating(rating)
            assert isinstance(rating.name, unicode), rating
            assert rating.label and rating.label != str(None), rating

    def test_dehydrate_ratings_body(self):

        for k, body in ratingsbodies.RATINGS_BODIES.iteritems():
            body = ratingsbodies.dehydrate_ratings_body(body)
            assert isinstance(body.name, unicode)
            assert body.label and body.label != str(None)
            assert isinstance(body.description, unicode)

    @contextmanager
    def tower_activate(self, region):
        try:
            activate(region)
            yield
        finally:
            activate('en-US')

    def test_dehydrate_rating_language(self):
        with self.tower_activate('es'):
            rating = ratingsbodies.dehydrate_rating(ratingsbodies.ESRB_T)
            eq_(rating.name, 'Adolescente')

        with self.tower_activate('fr'):
            rating = ratingsbodies.dehydrate_rating(ratingsbodies.ESRB_T)
            eq_(rating.name, 'Adolescents')

        rating = ratingsbodies.dehydrate_rating(ratingsbodies.ESRB_T)
        eq_(rating.name, 'Teen')
