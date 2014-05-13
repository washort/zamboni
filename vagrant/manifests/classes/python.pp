# Install python and compiled modules for project
class python {
    package {
        ["python2.7-dev", "python2.7", "libapache2-mod-wsgi", "python-pip",
         "libxml2-dev", "libxslt1-dev", "libssl-dev", "git-core",
         "python-twisted", "swig", "python-m2crypto", "libjpeg8-dev",
         "libpng12-dev", "libffi-dev"]:
             ensure => installed;
    }

    exec { "pip-install":
        command => "pip install --user --download-cache=/tmp/pip-cache --find-links https://pyrepo.addons.mozilla.org --no-deps --exists-action=w -r $PROJ_DIR/requirements/dev.txt",
        # Disable timeout. Pip has its own sensible timeouts.
        timeout => 0,
        logoutput => true,
        user => vagrant,
        require => Package["python2.7", "libxml2-dev", "libxslt1-dev",
                           "libssl-dev"]
    }
}
