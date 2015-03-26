from rest_framework.routers import SimpleRouter

from mkt.websites import views
websites = SimpleRouter()
websites.register('website', views.WebsiteViewSet, base_name='website')
