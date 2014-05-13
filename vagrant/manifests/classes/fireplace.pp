class fireplace {
    package {
        ["nodejs", "nodejs-legacy", "npm"]:
            ensure => installed;
    }
    exec { 'clone-fireplace':
        cwd => '/home/vagrant',
        command => 'git clone https://github.com/mozilla/fireplace/',
        user => vagrant,
        creates => '/home/vagrant/fireplace/README.md',
        require => Package['git-core']
    }

    exec {'install-fireplace-deps':
        cwd => '/home/vagrant/fireplace',
        command => 'npm install',
        user => vagrant,
        require => [Package['nodejs-legacy'], Exec['clone-fireplace']]
    }

    exec {'install-commonplace':
        cwd => '/home/vagrant/fireplace',
        command => 'npm install commonplace',
        user => vagrant,
        require => Exec['install-fireplace-deps']
    }

    exec {'fireplace-compile':
        cwd => '/home/vagrant/fireplace',
        command => '/home/vagrant/fireplace/node_modules/commonplace/bin/commonplace compile',
        user => vagrant,
        require => Exec['install-commonplace']
    }

    exec {'fireplace-includes':
        cwd => '/home/vagrant/fireplace',
        command => '/home/vagrant/fireplace/node_modules/commonplace/bin/commonplace includes',
        user => vagrant,
        require => Exec['install-commonplace'],
        creates => '/home/vagrant/fireplace/hearth/media/js/include.js'
    }
}
