# Commands to run before all others in puppet.
class init {
    group { "puppet":
        ensure => "present",
    }

    file { "$PROJ_DIR":
        ensure => link,
        target => "/vagrant",
        owner => vagrant
    }
    # If you haven't created a custom pp file, create one from dist.
    file { "$PROJ_DIR/vagrant/manifests/classes/custom.pp":
        ensure => file,
        source => "$PROJ_DIR/vagrant/manifests/classes/custom-dist.pp",
        replace => false,
        require => [ File["$PROJ_DIR"] ];
    }
}
