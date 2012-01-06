CREATE TABLE users_payment_details (
       `paypal_id` varchar(255) NOT NULL PRIMARY KEY,
       `paypal_permissions_token` varchar(255),
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
