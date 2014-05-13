
# Based loosely on https://gist.github.com/1190526
# This can probably be simplified when there is a deb package for ES.

class elasticsearch {
    package {
        ["openjdk-7-jre", "elasticsearch"]:
            ensure => installed,
    }

    file { "/etc/elasticsearch.yml":
        source => "$PROJ_DIR/scripts/elasticsearch/elasticsearch.yml",
        ensure => file,
        replace => true
        }

    exec { "install_service":
        # Makes /etc/init.d/elasticsearch
        command => 'sudo update-rc.d elasticsearch 95 10',
        # Install if service is not already running.
        unless => 'test -e /etc/init.d/elasticsearch',
        require => Package["openjdk-7-jre", "elasticsearch"]
    }

    service { "elasticsearch":
        enable => true,
        ensure => "running",
        require => Exec["install_service"]
    }
}
