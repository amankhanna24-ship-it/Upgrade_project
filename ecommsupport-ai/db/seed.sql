USE novashop;

INSERT INTO products (product_id, name, category, description, price_gbp, stock_qty, warranty_months) VALUES
('NS-LAP-001', 'NovaBook Pro 14',            'laptops',     '14-inch laptop, Intel Core Ultra 7, 16GB RAM, 1TB SSD, 2.8K OLED display, 18-hour battery.', 1299.00, 12, 12),
('NS-LAP-002', 'NovaBook Air 13',            'laptops',     '13-inch ultralight laptop, 8GB RAM, 512GB SSD, fanless design, 15-hour battery.',             849.00,  0, 12),
('NS-AUD-001', 'Aria Wireless Headphones',   'audio',       'Over-ear Bluetooth 5.3 headphones with active noise cancellation and 40-hour battery.',       179.00, 40, 12),
('NS-AUD-002', 'Aria Buds',                  'audio',       'True wireless earbuds, ANC, IPX4 sweat resistance, wireless charging case.',                    99.00, 65, 12),
('NS-PER-001', 'KeyFlow Wireless Keyboard',  'peripherals', 'Low-profile wireless keyboard, multi-device Bluetooth pairing (3 devices), backlit keys, USB-C.', 69.00, 120, 24),
('NS-PER-002', 'GlideMouse Ergonomic',       'peripherals', 'Vertical ergonomic wireless mouse, 4000 DPI, silent clicks, 70-day battery.',                   45.00, 80, 24),
('NS-MON-001', 'ClearView 27 4K Monitor',    'monitors',    '27-inch 4K IPS monitor, 99% sRGB, USB-C 90W power delivery, height-adjustable stand.',        349.00,  7, 36),
('NS-PHN-001', 'Nova X5 Smartphone',         'phones',      '6.4-inch AMOLED, 256GB storage, triple camera, 5G. Unlocked.',                                 699.00, 25, 12),
('NS-WEA-001', 'Pulse Smartwatch',           'wearables',   'Fitness smartwatch with GPS, heart-rate and SpO2 sensors, 7-day battery.',                     199.00, 30, 12),
('NS-ACC-001', 'PowerCore 20K Power Bank',   'accessories', '20,000mAh power bank, 65W USB-C PD, charges laptops.',                                         49.00,  0, 18);

INSERT INTO customers (customer_id, full_name, email, phone, shipping_address, loyalty_tier) VALUES
('CUST-001', 'Priya Sharma',   'priya.sharma@example.com',  '+44 7700 900101', '12 Elm Street, Leeds LS1 4AB',          'plus'),
('CUST-002', 'James Whitaker', 'j.whitaker@example.com',    '+44 7700 900202', '8 Harbour Road, Bristol BS1 5TT',       'standard'),
('CUST-003', 'Sofia Rossi',    'sofia.rossi@example.com',   '+44 7700 900303', 'Flat 3, 44 King Street, Manchester M2 4LQ', 'standard'),
('CUST-004', 'Arjun Mehta',    'arjun.mehta@example.com',   '+44 7700 900404', '5 Meadow Close, Cambridge CB2 1TN',     'plus'),
('CUST-005', 'Emma Clarke',    'emma.clarke@example.com',   '+44 7700 900505', '21 Castle Wynd, Edinburgh EH1 2NG',     'standard');

-- Dates are relative to load time so scenarios like "45 days after delivery" stay true.
INSERT INTO orders (order_id, customer_id, status, warranty_plan, carrier, tracking_number, total_gbp, placed_at, shipped_at, delivered_at, estimated_delivery) VALUES
('ORD-1001', 'CUST-001', 'shipped',    'none',          'DPD',        'DPD5839201', 179.00, NOW() - INTERVAL 3 DAY,  NOW() - INTERVAL 2 DAY,  NULL,                    CURDATE() + INTERVAL 1 DAY),
('ORD-1002', 'CUST-001', 'delivered',  'novacare',      'Royal Mail', 'RM44120983GB', 1299.00, NOW() - INTERVAL 50 DAY, NOW() - INTERVAL 48 DAY, NOW() - INTERVAL 45 DAY, CURDATE() - INTERVAL 45 DAY),
('ORD-1003', 'CUST-002', 'delayed',    'none',          'Evri',       'EVR77810234',  118.00, NOW() - INTERVAL 9 DAY,  NOW() - INTERVAL 7 DAY,  NULL,                    CURDATE() - INTERVAL 2 DAY),
('ORD-1004', 'CUST-002', 'delivered',  'none',          'DPD',        'DPD5839555',   69.00,  NOW() - INTERVAL 12 DAY, NOW() - INTERVAL 11 DAY, NOW() - INTERVAL 10 DAY, CURDATE() - INTERVAL 10 DAY),
('ORD-1005', 'CUST-003', 'processing', 'none',          NULL,         NULL,           349.00, NOW() - INTERVAL 1 DAY,  NULL,                    NULL,                    CURDATE() + INTERVAL 4 DAY),
('ORD-1006', 'CUST-003', 'delivered',  'novacare_plus', 'DPD',        'DPD5840012',   699.00, NOW() - INTERVAL 200 DAY, NOW() - INTERVAL 199 DAY, NOW() - INTERVAL 197 DAY, CURDATE() - INTERVAL 197 DAY),
('ORD-1007', 'CUST-004', 'delivered',  'none',          'Royal Mail', 'RM44121777GB', 179.00, NOW() - INTERVAL 20 DAY, NOW() - INTERVAL 19 DAY, NOW() - INTERVAL 18 DAY, CURDATE() - INTERVAL 18 DAY),
('ORD-1008', 'CUST-004', 'cancelled',  'none',          NULL,         NULL,           849.00, NOW() - INTERVAL 30 DAY, NULL,                    NULL,                    NULL),
('ORD-1009', 'CUST-005', 'delivered',  'none',          'Evri',       'EVR77819911',  244.00, NOW() - INTERVAL 5 DAY,  NOW() - INTERVAL 4 DAY,  NOW() - INTERVAL 2 DAY,  CURDATE() - INTERVAL 2 DAY),
('ORD-1010', 'CUST-005', 'returned',   'none',          'DPD',        'DPD5841000',   99.00,  NOW() - INTERVAL 40 DAY, NOW() - INTERVAL 39 DAY, NOW() - INTERVAL 37 DAY, CURDATE() - INTERVAL 37 DAY);

INSERT INTO order_items (order_id, product_id, quantity, unit_price_gbp) VALUES
('ORD-1001', 'NS-AUD-001', 1, 179.00),
('ORD-1002', 'NS-LAP-001', 1, 1299.00),
('ORD-1003', 'NS-PER-001', 1, 69.00),
('ORD-1003', 'NS-ACC-001', 1, 49.00),
('ORD-1004', 'NS-PER-001', 1, 69.00),
('ORD-1005', 'NS-MON-001', 1, 349.00),
('ORD-1006', 'NS-PHN-001', 1, 699.00),
('ORD-1007', 'NS-AUD-001', 1, 179.00),
('ORD-1008', 'NS-LAP-002', 1, 849.00),
('ORD-1009', 'NS-WEA-001', 1, 199.00),
('ORD-1009', 'NS-PER-002', 1, 45.00),
('ORD-1010', 'NS-AUD-002', 1, 99.00);
