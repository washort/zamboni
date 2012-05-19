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

CREATE TABLE `paypal_checkstatus` (
    `id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `addon_id` integer NOT NULL,
    `failure_data` longtext
)
;
ALTER TABLE `paypal_checkstatus` ADD CONSTRAINT `addon_id_refs_id_9c8e9c2` FOREIGN KEY (`addon_id`) REFERENCES `addons` (`id`);
