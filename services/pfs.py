from collections import defaultdict
from email.Utils import formatdate
import re
from string import Template
import sys
from time import time
from urlparse import parse_qsl


import commonware.log
import jinja2

from utils import log_configure

import settings_local as settings

# This has to be imported after the settings so statsd knows where to log to.
from django_statsd.clients import statsd

# Go configure the log.
log_configure()

error_log = commonware.log.getLogger('z.pfs')

xml_template = """\
<?xml version="1.0"?>
<RDF:RDF xmlns:RDF="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:pfs="http://www.mozilla.org/2004/pfs-rdf#">

  <RDF:Description about="urn:mozilla:plugin-results:$mimetype">
    <pfs:plugins><RDF:Seq>
        <RDF:li resource="urn:mozilla:plugin:$guid"/>
    </RDF:Seq></pfs:plugins>
  </RDF:Description>

  <RDF:Description about="urn:mozilla:plugin:$guid">
    <pfs:updates><RDF:Seq>
        <RDF:li resource="urn:mozilla:plugin:$guid:$version"/>
    </RDF:Seq></pfs:updates>
  </RDF:Description>

  <RDF:Description about="urn:mozilla:plugin:$guid:$version">
    <pfs:name>$name</pfs:name>
    <pfs:requestedMimetype>$mimetype</pfs:requestedMimetype>
    <pfs:guid>$guid</pfs:guid>
    <pfs:version>$version</pfs:version>
    <pfs:IconUrl>$iconUrl</pfs:IconUrl>
    <pfs:InstallerLocation>$InstallerLocation</pfs:InstallerLocation>
    <pfs:InstallerHash>$InstallerHash</pfs:InstallerHash>
    <pfs:XPILocation>$XPILocation</pfs:XPILocation>
    <pfs:InstallerShowsUI>$InstallerShowsUI</pfs:InstallerShowsUI>
    <pfs:manualInstallationURL>$manualInstallationURL</pfs:manualInstallationURL>
    <pfs:licenseURL>$licenseURL</pfs:licenseURL>
    <pfs:needsRestart>$needsRestart</pfs:needsRestart>
  </RDF:Description>

</RDF:RDF>
"""

flash_re = re.compile(r'^(Win|(PPC|Intel) Mac OS X|Linux.+(x86_64|i\d86))|SunOs', re.IGNORECASE)
quicktime_re = re.compile(r'^(application/(sdp|x-(mpeg|rtsp|sdp))|audio/(3gpp(2)?|AMR|aiff|basic|mid(i)?|mp4|mpeg|vnd\.qcelp|wav|x-(aiff|m4(a|b|p)|midi|mpeg|wav))|image/(pict|png|tiff|x-(macpaint|pict|png|quicktime|sgi|targa|tiff))|video/(3gpp(2)?|flc|mp4|mpeg|quicktime|sd-video|x-mpeg))$')
java_re = re.compile(r'^application/x-java-((applet|bean)(;jpi-version=1\.5|;version=(1\.(1(\.[1-3])?|(2|4)(\.[1-2])?|3(\.1)?|5)))?|vm)$')
wmp_re = re.compile(r'^(application/(asx|x-(mplayer2|ms-wmp))|video/x-ms-(asf(-plugin)?|wm(p|v|x)?|wvx)|audio/x-ms-w(ax|ma))$')


def get_output(data):
    g = defaultdict(str, [(k, jinja2.escape(v)) for k, v in data.iteritems()])

    required = ['mimetype', 'appID', 'appVersion', 'clientOS', 'chromeLocale']

    # Some defaults we override depending on what we find below.
    plugin = dict(mimetype='-1', name='-1', guid='-1', version='',
                  iconUrl='', XPILocation='', InstallerLocation='',
                  InstallerHash='', InstallerShowsUI='',
                  manualInstallationURL='', licenseURL='',
                  needsRestart='true')

    # Special case for mimetype if they are provided.
    plugin['mimetype'] = g['mimetype'] or '-1'

    output = Template(xml_template)

    for s in required:
        if s not in data:
            # A sort of 404, matching what was returned in the original PHP.
            return output.substitute(plugin)

    # Figure out what plugins we've got, and what plugins we know where
    # to get.

    # Begin our huge and embarrassing if-else statement.
    if (g['mimetype'] in ['application/x-shockwave-flash',
                          'application/futuresplash'] and
        re.match(flash_re, g['clientOS'])):

        # Tell the user where they can go to get the installer.

        plugin.update(
            name='Adobe Flash Player',
            manualInstallationURL='http://www.adobe.com/go/getflashplayer')

        # Offer Windows users a specific flash plugin installer instead.
        # Don't use a https URL for the license here, per request from
        # Macromedia.

        if g['clientOS'].startswith('Win'):
            plugin.update(
                guid='{4cfaef8a-a6c9-41a0-8e6f-967eb8f49143}',
                XPILocation='',
                iconUrl='http://fpdownload2.macromedia.com/pub/flashplayer/current/fp_win_installer.ico',
                needsRestart='false',
                InstallerShowsUI='true',
                version='12.0.0.44',
                InstallerHash='sha256:41f6636e383fa814b8ab5914d09a30dafbcc83265915ff34ec1896abe3880ba3',
                InstallerLocation='http://download.macromedia.com/pub/flashplayer/pdc/fp_pl_pfs_installer.exe')

    elif (g['mimetype'] == 'application/x-director' and
          g['clientOS'].startswith('Win')):
        plugin.update(
            name='Adobe Shockwave Player',
            manualInstallationURL='http://get.adobe.com/shockwave/otherversions')

        # Even though the shockwave installer is not a silent installer, we
        # need to show its EULA here since we've got a slimmed down
        # installer that doesn't do that itself.
        if g['chromeLocale'] != 'ja-JP':
            plugin.update(
                licenseURL='http://www.adobe.com/go/eula_shockwaveplayer')
        else:
            plugin.update(
                licenseURL='http://www.adobe.com/go/eula_shockwaveplayer_jp')
        plugin.update(
            guid='{45f2a22c-4029-4209-8b3d-1421b989633f}',
            XPILocation='',
            version='12.0.7.148',
            InstallerHash='sha256:2c439b90132b7283d8c0d139ca4768f20219f40dec0f42a2c4e1b0be1626e6aa',
            InstallerLocation='http://fpdownload.macromedia.com/pub/shockwave/default/english/win95nt/latest/Shockwave_Installer_FF.exe',
            manualInstallationURL='http://get.adobe.com/shockwave/otherversions',
            needsRestart='false',
            InstallerShowsUI='false')

    elif (g['mimetype'] in ['audio/x-pn-realaudio-plugin',
                            'audio/x-pn-realaudio'] and
          re.match(r'^(Win|Linux|PPC Mac OS X)', g['clientOS'])):
        plugin.update(
            name='Real Player',
            version='10.5',
            manualInstallationURL='http://www.real.com')

        if g['clientOS'].startswith('Win'):
            plugin.update(
                XPILocation='http://forms.real.com/real/player/download.html?type=firefox',
                guid='{d586351c-cb55-41a7-8e7b-4aaac5172d39}')
        else:
            plugin.update(
                guid='{269eb771-59de-4702-9209-ca97ce522f6d}')

    elif (re.match(quicktime_re, g['mimetype']) and
          re.match(r'^(Win|PPC Mac OS X)', g['clientOS'])):

        # Well, we don't have a plugin that can handle any of those
        # mimetypes, but the Apple Quicktime plugin can. Point the user to
        # the Quicktime download page.

        plugin.update(
            name='Apple Quicktime',
            guid='{a42bb825-7eee-420f-8ee7-834062b6fefd}',
            InstallerShowsUI='true',
            manualInstallationURL='http://www.apple.com/quicktime/download/')

    elif (re.match(java_re, g['mimetype']) and
          re.match(r'^(Win|Linux|PPC Mac OS X)', g['clientOS'])):

        # We serve up the Java plugin for the following mimetypes:
        #
        # application/x-java-vm
        # application/x-java-applet;jpi-version=1.5
        # application/x-java-bean;jpi-version=1.5
        # application/x-java-applet;version=1.3
        # application/x-java-bean;version=1.3
        # application/x-java-applet;version=1.2.2
        # application/x-java-bean;version=1.2.2
        # application/x-java-applet;version=1.2.1
        # application/x-java-bean;version=1.2.1
        # application/x-java-applet;version=1.4.2
        # application/x-java-bean;version=1.4.2
        # application/x-java-applet;version=1.5
        # application/x-java-bean;version=1.5
        # application/x-java-applet;version=1.3.1
        # application/x-java-bean;version=1.3.1
        # application/x-java-applet;version=1.4
        # application/x-java-bean;version=1.4
        # application/x-java-applet;version=1.4.1
        # application/x-java-bean;version=1.4.1
        # application/x-java-applet;version=1.2
        # application/x-java-bean;version=1.2
        # application/x-java-applet;version=1.1.3
        # application/x-java-bean;version=1.1.3
        # application/x-java-applet;version=1.1.2
        # application/x-java-bean;version=1.1.2
        # application/x-java-applet;version=1.1.1
        # application/x-java-bean;version=1.1.1
        # application/x-java-applet;version=1.1
        # application/x-java-bean;version=1.1
        # application/x-java-applet
        # application/x-java-bean
        #
        #
        # We don't want to link users directly to the Java plugin because
        # we want to warn them about ongoing security problems first. Link
        # to SUMO.

        plugin.update(
            name='Java Runtime Environment',
            manualInstallationURL='https://support.mozilla.org/kb/use-java-plugin-to-view-interactive-content',
            needsRestart='false',
            guid='{fbe640ef-4375-4f45-8d79-767d60bf75b8}')

    elif (g['mimetype'] in ['application/pdf', 'application/vnd.fdf',
                            'application/vnd.adobe.xfdf',
                            'application/vnd.adobe.xdp+xml',
                            'application/vnd.adobe.xfd+xml'] and
          re.match(r'^(Win|PPC Mac OS X|Linux(?! x86_64))', g['clientOS'])):
        plugin.update(
            name='Adobe Acrobat Plug-In',
            guid='{d87cd824-67cb-4547-8587-616c70318095}',
            manualInstallationURL='http://www.adobe.com/products/acrobat/readstep.html')

    elif (g['mimetype'] == 'application/x-mtx' and
          re.match(r'^(Win|PPC Mac OS X)', g['clientOS'])):
        plugin.update(
            name='Viewpoint Media Player',
            guid='{03f998b2-0e00-11d3-a498-00104b6eb52e}',
            manualInstallationURL='http://www.viewpoint.com/pub/products/vmp.html')

    elif re.match(wmp_re, g['mimetype']):
        # We serve up the Windows Media Player plugin for the following
        # mimetypes:
        #
        # application/asx
        # application/x-mplayer2
        # audio/x-ms-wax
        # audio/x-ms-wma
        # video/x-ms-asf
        # video/x-ms-asf-plugin
        # video/x-ms-wm
        # video/x-ms-wmp
        # video/x-ms-wmv
        # video/x-ms-wmx
        # video/x-ms-wvx
        #
        # For all windows users who don't have the WMP 11 plugin, give them
        # a link for it.
        if g['clientOS'].startswith('Win'):
            plugin.update(
                name='Windows Media Player',
                version='11',
                guid='{cff1240a-fd24-4b9f-8183-ccd96e5300d0}',
                manualInstallationURL='http://port25.technet.com/pages/windows-media-player-firefox-plugin-download.aspx')

        # For OSX users -- added Intel to this since flip4mac is a UB.
        # Contact at MS was okay w/ this, plus MS points to this anyway.
        elif re.match(r'^(PPC|Intel) Mac OS X', g['clientOS']):
            plugin.update(
                name='Flip4Mac',
                version='2.1',
                guid='{cff0240a-fd24-4b9f-8183-ccd96e5300d0}',
                manualInstallationURL='http://www.flip4mac.com/wmv_download.htm')

    elif (g['mimetype'] == 'application/x-xstandard' and
          re.match(r'^(Win|PPC Mac OS X)', g['clientOS'])):
        plugin.update(
            name='XStandard XHTML WYSIWYG Editor',
            guid='{3563d917-2f44-4e05-8769-47e655e92361}',
            iconUrl='http://xstandard.com/images/xicon32x32.gif',
            XPILocation='http://xstandard.com/download/xstandard.xpi',
            InstallerShowsUI='false',
            manualInstallationURL='http://xstandard.com/download/',
            licenseURL='http://xstandard.com/license/')

    elif (g['mimetype'] == 'application/x-dnl' and
          g['clientOS'].startswith('Win')):
        plugin.update(
            name='DNL Reader',
            guid='{ce9317a3-e2f8-49b9-9b3b-a7fb5ec55161}',
            version='5.5',
            iconUrl='http://digitalwebbooks.com/reader/dwb16.gif',
            XPILocation='http://digitalwebbooks.com/reader/xpinst.xpi',
            InstallerShowsUI='false',
            manualInstallationURL='http://digitalwebbooks.com/reader/')

    elif (g['mimetype'] == 'application/x-videoegg-loader' and
          g['clientOS'].startswith('Win')):
        plugin.update(
            name='VideoEgg Publisher',
            guid='{b8b881f0-2e07-11db-a98b-0800200c9a66}',
            iconUrl='http://videoegg.com/favicon.ico',
            XPILocation='http://update.videoegg.com/Install/Windows/Initial/VideoEggPublisher.xpi',
            InstallerShowsUI='true',
            manualInstallationURL='http://www.videoegg.com/')

    elif (g['mimetype'] == 'video/vnd.divx' and
          g['clientOS'].startswith('Win')):
        plugin.update(
            name='DivX Web Player',
            guid='{a8b771f0-2e07-11db-a98b-0800200c9a66}',
            iconUrl='http://images.divx.com/divx/player/webplayer.png',
            XPILocation='http://download.divx.com/player/DivXWebPlayer.xpi',
            InstallerShowsUI='false',
            licenseURL='http://go.divx.com/plugin/license/',
            manualInstallationURL='http://go.divx.com/plugin/download/')

    elif (g['mimetype'] == 'video/vnd.divx' and
          re.match(r'^(PPC|Intel) Mac OS X', g['clientOS'])):
        plugin.update(
            name='DivX Web Player',
            guid='{a8b771f0-2e07-11db-a98b-0800200c9a66}',
            iconUrl='http://images.divx.com/divx/player/webplayer.png',
            XPILocation='http://download.divx.com/player/DivXWebPlayerMac.xpi',
            InstallerShowsUI='false',
            licenseURL='http://go.divx.com/plugin/license/',
            manualInstallationURL='http://go.divx.com/plugin/download/')

    # End ridiculously huge and embarrassing if-else block.
    return output.substitute(plugin)


def format_date(secs):
    return '%s GMT' % formatdate(time() + secs)[:25]


def get_headers(length):
    return [('Content-Type', 'text/xml; charset=utf-8'),
            ('Cache-Control', 'public, max-age=3600'),
            ('Last-Modified', format_date(0)),
            ('Expires', format_date(3600)),
            ('Content-Length', str(length))]


def log_exception(data):
    (typ, value, traceback) = sys.exc_info()
    error_log.error(u'Type: %s, %s. Query: %s' % (typ, value, data))


def application(environ, start_response):
    status = '200 OK'

    with statsd.timer('services.pfs'):
        data = dict(parse_qsl(environ['QUERY_STRING']))
        try:
            output = get_output(data).encode('utf-8')
            start_response(status, get_headers(len(output)))
        except:
            log_exception(data)
            raise
        return [output]
