# Migrate the database
class migrate {

    # # Skip this migration because it won't succeed without indexes but you
    # # can't build indexes without running migrations :(
    # file { "$PROJ_DIR/migrations/264-locale-indexes.py":
    #     content => "def run(): pass",
    #     replace => true
    # }

    exec { "sql_migrate":
        cwd => "$PROJ_DIR",
        user => vagrant,
        command => "/home/vagrant/.local/bin/schematic migrations/",
        logoutput => true,
        require => [
            Service["mysql"],
            Package["python2.7"],
            File["$PROJ_DIR/settings_local_mkt.py"],
       #     File["$PROJ_DIR/migrations/264-locale-indexes.py"],
            Exec["fetch_landfill_sql"],
            Exec["load_data"]
        ];
    }

    # exec { "restore_migration_264":
    #     cwd => "$PROJ_DIR",
    #     command => "git checkout migrations/264-locale-indexes.py",
    #     require => [Exec["sql_migrate"], Package["git-core"]]
    # }
}
