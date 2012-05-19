CREATE TABLE `compat_totals` (
       `app` integer NOT NULL PRIMARY KEY,
       `totals` integer
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `fake_email` (
       `id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY,
       `message` text NOT NULL,
       `created` datetime NOT NULL default '0000-00-00 00:00:00',
       `modified` datetime NOT NULL default '0000-00-00 00:00:00',
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
