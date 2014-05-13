class launch {
    package { 'cryptography':
        provider => pip
    }
    file { '/home/vagrant/server.pem':
        ensure => file,
        source => "$PROJ_DIR/vagrant/files/home/vagrant/server.pem",
        replace => false,
        require => [ File["$PROJ_DIR"] ];
    }

    exec { 'zamboni-server':
        command => "$PROJ_DIR/scripts/start.sh",
        cwd => '/home/vagrant/project',
        user => vagrant,
        require => [
                    File['/home/vagrant/server.pem'],
                    Exec['fireplace-compile', 'fireplace-includes'],
                    Package['cryptography']
                    ]
    }
}
