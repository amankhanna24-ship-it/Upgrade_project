-- eCommSupport-AI: structured data for NovaShop
-- MySQL 8 / MariaDB 10.6+

CREATE DATABASE IF NOT EXISTS novashop CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE novashop;

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS products;

CREATE TABLE products (
    product_id      VARCHAR(16)   PRIMARY KEY,          -- e.g. NS-LAP-001
    name            VARCHAR(120)  NOT NULL,
    category        VARCHAR(40)   NOT NULL,             -- laptops, audio, peripherals ...
    description     TEXT          NOT NULL,
    price_gbp       DECIMAL(10,2) NOT NULL CHECK (price_gbp >= 0),
    stock_qty       INT           NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    warranty_months INT           NOT NULL DEFAULT 12,  -- manufacturer warranty
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    FULLTEXT KEY ft_products (name, description)
) ENGINE=InnoDB;

CREATE TABLE customers (
    customer_id     VARCHAR(12)   PRIMARY KEY,          -- e.g. CUST-001
    full_name       VARCHAR(120)  NOT NULL,
    email           VARCHAR(160)  NOT NULL UNIQUE,
    phone           VARCHAR(32),
    shipping_address VARCHAR(255) NOT NULL,
    loyalty_tier    ENUM('standard','plus') NOT NULL DEFAULT 'standard',
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE orders (
    order_id        VARCHAR(16)   PRIMARY KEY,          -- e.g. ORD-1001
    customer_id     VARCHAR(12)   NOT NULL,
    status          ENUM('processing','shipped','delivered','delayed','cancelled','returned') NOT NULL,
    warranty_plan   ENUM('none','novacare','novacare_plus') NOT NULL DEFAULT 'none',
    carrier         VARCHAR(40),
    tracking_number VARCHAR(40),
    total_gbp       DECIMAL(10,2) NOT NULL,
    placed_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    shipped_at      DATETIME      NULL,
    delivered_at    DATETIME      NULL,
    estimated_delivery DATE       NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    INDEX idx_orders_customer (customer_id)
) ENGINE=InnoDB;

CREATE TABLE order_items (
    order_id        VARCHAR(16)   NOT NULL,
    product_id      VARCHAR(16)   NOT NULL,
    quantity        INT           NOT NULL CHECK (quantity > 0),
    unit_price_gbp  DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (order_id, product_id),
    FOREIGN KEY (order_id)   REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB;

-- Least-privilege account for the MCP server. It can read everything it needs
-- and write orders, but it cannot alter schema, drop tables or touch grants.
CREATE USER IF NOT EXISTS 'novashop_mcp'@'%' IDENTIFIED BY 'change-me';
GRANT SELECT ON novashop.* TO 'novashop_mcp'@'%';
GRANT INSERT ON novashop.orders TO 'novashop_mcp'@'%';
GRANT INSERT ON novashop.order_items TO 'novashop_mcp'@'%';
GRANT UPDATE (stock_qty) ON novashop.products TO 'novashop_mcp'@'%';
-- MySQL 8.0.22+ needs this (or full UPDATE) for SELECT ... FOR UPDATE in place_order.
GRANT LOCK TABLES ON novashop.* TO 'novashop_mcp'@'%';
FLUSH PRIVILEGES;
-- Running MySQL/MariaDB natively rather than in Docker? Repeat the CREATE USER and GRANTs
-- above for 'novashop_mcp'@'localhost', because the anonymous localhost user can shadow '%'.
