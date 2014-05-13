# playdoh-specific commands that get zamboni all going so you don't
# have to.

# TODO: Make this rely on things that are not straight-up exec.
class zamboni {
    package { "wget":
        ensure => installed;
    }

    file { "$PROJ_DIR/settings_local_mkt.py":
        ensure => file,
        source => "$PROJ_DIR/docs/settings/settings_local.dev.py",
        replace => false;
    }
      exec {"install-stylus":
           cwd => "$PROJ_DIR",
           command => "npm install stylus less",
           user => vagrant,
           creates => ["$PROJ_DIR/node_modules/stylus/bin/stylus",
                       "$PROJ_DIR/node_modules/less/bin/lessc"],
           require => [Package["nodejs-legacy"],
                       File["$PROJ_DIR/settings_local_mkt.py"]]
           }

    exec { "create_mysql_database":
        command => "mysqladmin -uroot create $DB_NAME",
        unless  => "mysql -uroot -B --skip-column-names -e 'show databases' | /bin/grep '$DB_NAME'",
        require => Exec["install-stylus"]

    }

    exec { "grant_mysql_database":
        command => "mysql -uroot -B -e'GRANT ALL PRIVILEGES ON $DB_NAME.* TO $DB_USER@localhost # IDENTIFIED BY \"$DB_PASS\"'",
        unless  => "mysql -uroot -B --skip-column-names mysql -e 'select user from user' | grep '$DB_USER'",
        require => Exec["create_mysql_database"];
    }

    exec { "fetch_landfill_sql":
        cwd => "$PROJ_DIR",
        command => "wget --no-check-certificate -P /tmp https://landfill-mkt.allizom.org/db_data/landfill-`date +%Y-%m-%d`.sql.gz",
        environment => ["TZ=PDT7PST"],
        require => [
            Package["wget"],
            Exec["grant_mysql_database"]
        ];
    }

    exec { "load_data":
        cwd => "$PROJ_DIR",
        command => "zcat /tmp/landfill-`date +%Y-%m-%d`.sql.gz | mysql -u$DB_USER $DB_NAME",
        environment => ["TZ=PDT7PST"],
        require => [
            Exec["fetch_landfill_sql"]
        ];
    }

    # TODO(Kumar) add landfill files as well.
}
