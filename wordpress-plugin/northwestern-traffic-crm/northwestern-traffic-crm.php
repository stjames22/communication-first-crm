<?php
/**
 * Plugin Name: Northwestern Traffic CRM
 * Description: Lightweight website traffic, easy-link tracking, and communications CRM dashboard for an insurance group.
 * Version: 0.3.0
 * Author: Northwestern Insurance Group
 * Text Domain: northwestern-traffic-crm
 */

if (!defined('ABSPATH')) {
    exit;
}

final class Northwestern_Traffic_CRM {
    private const VERSION = '0.3.0';
    private const COOKIE = 'nwtc_visitor';
    private const NONCE_ACTION = 'nwtc_admin_action';
    private const OPTION_SETTINGS = 'nwtc_settings';

    private static ?Northwestern_Traffic_CRM $instance = null;

    public static function instance(): Northwestern_Traffic_CRM {
        if (self::$instance === null) {
            self::$instance = new self();
        }

        return self::$instance;
    }

    private function __construct() {
        register_activation_hook(__FILE__, [$this, 'activate']);
        add_action('init', [$this, 'register_shortcodes']);
        add_action('wp', [$this, 'record_visit']);
        add_action('template_redirect', [$this, 'handle_link_redirect']);
        add_action('admin_menu', [$this, 'admin_menu']);
        add_action('admin_enqueue_scripts', [$this, 'admin_assets']);
        add_action('wp_enqueue_scripts', [$this, 'public_assets']);
        add_action('admin_post_nwtc_save_settings', [$this, 'save_settings']);
        add_action('admin_post_nwtc_save_link', [$this, 'save_link']);
        add_action('admin_post_nwtc_delete_link', [$this, 'delete_link']);
        add_action('admin_post_nwtc_export_contacts', [$this, 'export_contacts_csv']);
        add_action('admin_post_nopriv_nwtc_lead', [$this, 'save_public_lead']);
        add_action('admin_post_nwtc_lead', [$this, 'save_public_lead']);
        add_action('admin_post_nwtc_save_contact', [$this, 'save_contact']);
        add_action('admin_post_nwtc_save_note', [$this, 'save_note']);
    }

    public function activate(): void {
        global $wpdb;

        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        $charset = $wpdb->get_charset_collate();
        $visits = $this->table('visits');
        $links = $this->table('links');
        $clicks = $this->table('clicks');
        $contacts = $this->table('contacts');
        $notes = $this->table('notes');

        dbDelta("CREATE TABLE {$visits} (
            id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
            visitor_id varchar(64) NOT NULL,
            page_url text NOT NULL,
            page_title varchar(255) DEFAULT '',
            referrer text DEFAULT NULL,
            utm_source varchar(120) DEFAULT '',
            utm_medium varchar(120) DEFAULT '',
            utm_campaign varchar(120) DEFAULT '',
            ip_hash varchar(64) DEFAULT '',
            user_agent text DEFAULT NULL,
            created_at datetime NOT NULL,
            PRIMARY KEY (id),
            KEY visitor_id (visitor_id),
            KEY created_at (created_at)
        ) {$charset};");

        dbDelta("CREATE TABLE {$links} (
            id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
            link_key varchar(80) NOT NULL,
            label varchar(190) NOT NULL,
            destination_url text NOT NULL,
            campaign varchar(120) DEFAULT '',
            active tinyint(1) NOT NULL DEFAULT 1,
            created_at datetime NOT NULL,
            updated_at datetime NOT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY link_key (link_key),
            KEY active (active)
        ) {$charset};");

        dbDelta("CREATE TABLE {$clicks} (
            id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
            link_id bigint(20) unsigned NOT NULL,
            visitor_id varchar(64) NOT NULL,
            referrer text DEFAULT NULL,
            ip_hash varchar(64) DEFAULT '',
            user_agent text DEFAULT NULL,
            created_at datetime NOT NULL,
            PRIMARY KEY (id),
            KEY link_id (link_id),
            KEY created_at (created_at)
        ) {$charset};");

        dbDelta("CREATE TABLE {$contacts} (
            id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
            name varchar(190) NOT NULL,
            email varchar(190) DEFAULT '',
            phone varchar(80) DEFAULT '',
            status varchar(40) NOT NULL DEFAULT 'lead',
            source varchar(120) DEFAULT '',
            last_touch datetime DEFAULT NULL,
            created_at datetime NOT NULL,
            updated_at datetime NOT NULL,
            PRIMARY KEY (id),
            KEY email (email),
            KEY phone (phone),
            KEY status (status)
        ) {$charset};");

        dbDelta("CREATE TABLE {$notes} (
            id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
            contact_id bigint(20) unsigned NOT NULL,
            note_type varchar(40) NOT NULL DEFAULT 'note',
            body text NOT NULL,
            created_by bigint(20) unsigned DEFAULT NULL,
            created_at datetime NOT NULL,
            PRIMARY KEY (id),
            KEY contact_id (contact_id),
            KEY created_at (created_at)
        ) {$charset};");

        if (!get_option(self::OPTION_SETTINGS)) {
            add_option(self::OPTION_SETTINGS, [
                'group_name' => 'Northwestern Insurance Group',
                'lead_email' => get_option('admin_email'),
                'retention_days' => 180,
                'crm_sync_enabled' => 0,
                'crm_sync_traffic' => 0,
                'crm_base_url' => '',
                'crm_api_key' => '',
            ]);
        }
    }

    public function register_shortcodes(): void {
        add_shortcode('nw_easy_link', [$this, 'easy_link_shortcode']);
        add_shortcode('nw_lead_form', [$this, 'lead_form_shortcode']);
    }

    public function admin_menu(): void {
        add_menu_page(
            __('Traffic CRM', 'northwestern-traffic-crm'),
            __('Traffic CRM', 'northwestern-traffic-crm'),
            'manage_options',
            'nwtc-dashboard',
            [$this, 'render_dashboard'],
            'dashicons-chart-area',
            26
        );

        add_submenu_page('nwtc-dashboard', __('Contacts', 'northwestern-traffic-crm'), __('Contacts', 'northwestern-traffic-crm'), 'manage_options', 'nwtc-contacts', [$this, 'render_contacts']);
        add_submenu_page('nwtc-dashboard', __('Easy Links', 'northwestern-traffic-crm'), __('Easy Links', 'northwestern-traffic-crm'), 'manage_options', 'nwtc-links', [$this, 'render_links']);
        add_submenu_page('nwtc-dashboard', __('Settings', 'northwestern-traffic-crm'), __('Settings', 'northwestern-traffic-crm'), 'manage_options', 'nwtc-settings', [$this, 'render_settings']);
    }

    public function admin_assets(string $hook): void {
        if (strpos($hook, 'nwtc') === false) {
            return;
        }

        wp_enqueue_style('nwtc-admin', plugin_dir_url(__FILE__) . 'assets/admin.css', [], self::VERSION);
        wp_enqueue_script('nwtc-admin', plugin_dir_url(__FILE__) . 'assets/admin.js', [], self::VERSION, true);
    }

    public function public_assets(): void {
        wp_enqueue_style('nwtc-public', plugin_dir_url(__FILE__) . 'assets/public.css', [], self::VERSION);
    }

    public function record_visit(): void {
        if (is_admin() || wp_doing_ajax() || wp_is_json_request() || is_feed() || is_robots()) {
            return;
        }

        if ($this->is_bot()) {
            return;
        }

        global $wpdb;

        $visitor_id = $this->visitor_id();
        $page_url = $this->current_url();
        $post_id = get_queried_object_id();
        $title = $post_id ? get_the_title($post_id) : wp_parse_url($page_url, PHP_URL_PATH);
        $now = current_time('mysql');

        $wpdb->insert($this->table('visits'), [
            'visitor_id' => $visitor_id,
            'page_url' => esc_url_raw($page_url),
            'page_title' => sanitize_text_field((string) $title),
            'referrer' => isset($_SERVER['HTTP_REFERER']) ? esc_url_raw(wp_unslash($_SERVER['HTTP_REFERER'])) : '',
            'utm_source' => isset($_GET['utm_source']) ? sanitize_text_field(wp_unslash($_GET['utm_source'])) : '',
            'utm_medium' => isset($_GET['utm_medium']) ? sanitize_text_field(wp_unslash($_GET['utm_medium'])) : '',
            'utm_campaign' => isset($_GET['utm_campaign']) ? sanitize_text_field(wp_unslash($_GET['utm_campaign'])) : '',
            'ip_hash' => $this->ip_hash(),
            'user_agent' => $this->user_agent(),
            'created_at' => $now,
        ], ['%s', '%s', '%s', '%s', '%s', '%s', '%s', '%s', '%s', '%s']);

        if ($this->crm_syncs_traffic()) {
            $this->crm_post('/api/wp/traffic-event', [
                'event_type' => 'visit',
                'source_system' => 'wordpress',
                'page_url' => esc_url_raw($page_url),
                'page_title' => sanitize_text_field((string) $title),
                'referrer' => isset($_SERVER['HTTP_REFERER']) ? esc_url_raw(wp_unslash($_SERVER['HTTP_REFERER'])) : '',
                'visitor_id_hash' => $this->visitor_id_hash($visitor_id),
                'campaign' => isset($_GET['utm_campaign']) ? sanitize_text_field(wp_unslash($_GET['utm_campaign'])) : '',
                'metadata' => [
                    'utm_source' => isset($_GET['utm_source']) ? sanitize_text_field(wp_unslash($_GET['utm_source'])) : '',
                    'utm_medium' => isset($_GET['utm_medium']) ? sanitize_text_field(wp_unslash($_GET['utm_medium'])) : '',
                ],
            ]);
        }

        $this->maybe_cleanup();
    }

    public function handle_link_redirect(): void {
        if (!isset($_GET['nwlink'])) {
            return;
        }

        global $wpdb;

        $key = sanitize_key(wp_unslash($_GET['nwlink']));
        $link = $wpdb->get_row($wpdb->prepare("SELECT * FROM {$this->table('links')} WHERE link_key = %s AND active = 1", $key));
        if (!$link) {
            wp_die(esc_html__('This tracked link is unavailable.', 'northwestern-traffic-crm'), 404);
        }

        $wpdb->insert($this->table('clicks'), [
            'link_id' => (int) $link->id,
            'visitor_id' => $this->visitor_id(),
            'referrer' => isset($_SERVER['HTTP_REFERER']) ? esc_url_raw(wp_unslash($_SERVER['HTTP_REFERER'])) : '',
            'ip_hash' => $this->ip_hash(),
            'user_agent' => $this->user_agent(),
            'created_at' => current_time('mysql'),
        ], ['%d', '%s', '%s', '%s', '%s', '%s']);

        $this->crm_post('/api/wp/easy-link-click', [
            'source_system' => 'wordpress',
            'link_key' => $link->link_key,
            'link_label' => $link->label,
            'campaign' => $link->campaign,
            'destination_url' => $link->destination_url,
            'referrer' => isset($_SERVER['HTTP_REFERER']) ? esc_url_raw(wp_unslash($_SERVER['HTTP_REFERER'])) : '',
            'visitor_id_hash' => $this->visitor_id_hash($this->visitor_id()),
        ]);

        wp_redirect(esc_url_raw($link->destination_url));
        exit;
    }

    public function easy_link_shortcode(array $atts): string {
        $atts = shortcode_atts([
            'key' => '',
            'label' => '',
            'class' => 'nw-easy-link',
        ], $atts, 'nw_easy_link');

        $key = sanitize_key($atts['key']);
        if (!$key) {
            return '';
        }

        global $wpdb;
        $link = $wpdb->get_row($wpdb->prepare("SELECT * FROM {$this->table('links')} WHERE link_key = %s AND active = 1", $key));
        if (!$link) {
            return '';
        }

        $label = $atts['label'] ? sanitize_text_field($atts['label']) : $link->label;
        $url = add_query_arg('nwlink', rawurlencode($link->link_key), home_url('/'));

        return sprintf(
            '<a class="%s" href="%s">%s</a>',
            esc_attr($atts['class']),
            esc_url($url),
            esc_html($label)
        );
    }

    public function lead_form_shortcode(array $atts): string {
        $atts = shortcode_atts([
            'source' => 'website',
            'button' => 'Request follow-up',
        ], $atts, 'nw_lead_form');

        $action = esc_url(admin_url('admin-post.php'));
        $source = esc_attr(sanitize_text_field($atts['source']));
        $button = esc_html(sanitize_text_field($atts['button']));

        ob_start();
        ?>
        <form class="nwtc-lead-form" method="post" action="<?php echo $action; ?>">
            <input type="hidden" name="action" value="nwtc_lead">
            <input type="hidden" name="source" value="<?php echo $source; ?>">
            <?php wp_nonce_field('nwtc_public_lead', 'nwtc_nonce'); ?>
            <label>
                <span><?php esc_html_e('Name', 'northwestern-traffic-crm'); ?></span>
                <input name="name" required autocomplete="name">
            </label>
            <label>
                <span><?php esc_html_e('Email', 'northwestern-traffic-crm'); ?></span>
                <input name="email" type="email" autocomplete="email">
            </label>
            <label>
                <span><?php esc_html_e('Phone', 'northwestern-traffic-crm'); ?></span>
                <input name="phone" autocomplete="tel">
            </label>
            <label>
                <span><?php esc_html_e('Message', 'northwestern-traffic-crm'); ?></span>
                <textarea name="message" rows="4"></textarea>
            </label>
            <button type="submit"><?php echo $button; ?></button>
        </form>
        <?php
        return (string) ob_get_clean();
    }

    public function render_dashboard(): void {
        $this->require_admin();
        $metrics = $this->dashboard_metrics();
        $pages = $this->top_pages();
        $sources = $this->top_sources();
        $links = $this->top_links();
        $pipeline = $this->contact_pipeline();
        $activity = $this->recent_activity();
        $recent_contacts = $this->recent_contacts(6);
        ?>
        <div class="wrap nwtc-wrap">
            <header class="nwtc-header">
                <div>
                    <p><?php echo esc_html($this->settings()['group_name']); ?></p>
                    <h1><?php esc_html_e('Traffic CRM Dashboard', 'northwestern-traffic-crm'); ?></h1>
                </div>
                <a class="button button-primary" href="<?php echo esc_url(admin_url('admin.php?page=nwtc-contacts')); ?>"><?php esc_html_e('Add Contact', 'northwestern-traffic-crm'); ?></a>
            </header>

            <section class="nwtc-grid nwtc-metrics">
                <?php foreach ($metrics as $label => $value) : ?>
                    <article class="nwtc-card">
                        <span><?php echo esc_html($label); ?></span>
                        <strong><?php echo esc_html((string) $value); ?></strong>
                    </article>
                <?php endforeach; ?>
            </section>

            <section class="nwtc-columns">
                <article class="nwtc-panel">
                    <h2><?php esc_html_e('Top pages', 'northwestern-traffic-crm'); ?></h2>
                    <?php $this->render_table($pages, ['page_title' => 'Page', 'views' => 'Views']); ?>
                </article>
                <article class="nwtc-panel">
                    <h2><?php esc_html_e('Traffic sources', 'northwestern-traffic-crm'); ?></h2>
                    <?php $this->render_table($sources, ['source' => 'Source', 'views' => 'Views']); ?>
                </article>
            </section>

            <section class="nwtc-columns">
                <article class="nwtc-panel">
                    <h2><?php esc_html_e('Easy Link performance', 'northwestern-traffic-crm'); ?></h2>
                    <?php $this->render_table($links, ['label' => 'Link', 'campaign' => 'Campaign', 'clicks' => 'Clicks']); ?>
                </article>
                <article class="nwtc-panel">
                    <h2><?php esc_html_e('Contact pipeline', 'northwestern-traffic-crm'); ?></h2>
                    <?php $this->render_pipeline($pipeline); ?>
                </article>
            </section>

            <section class="nwtc-panel">
                <h2><?php esc_html_e('Recent contacts', 'northwestern-traffic-crm'); ?></h2>
                <?php $this->render_contacts_table($recent_contacts); ?>
            </section>

            <section class="nwtc-panel">
                <h2><?php esc_html_e('Recent communications', 'northwestern-traffic-crm'); ?></h2>
                <?php $this->render_activity($activity); ?>
            </section>
        </div>
        <?php
    }

    public function render_contacts(): void {
        $this->require_admin();
        $contacts = $this->recent_contacts(50);
        $selected = isset($_GET['contact_id']) ? absint($_GET['contact_id']) : 0;
        $notes = $selected ? $this->contact_notes($selected) : [];
        ?>
        <div class="wrap nwtc-wrap">
            <header class="nwtc-header">
                <div>
                    <p><?php esc_html_e('Simple CRM', 'northwestern-traffic-crm'); ?></p>
                    <h1><?php esc_html_e('Contacts & Communications', 'northwestern-traffic-crm'); ?></h1>
                </div>
                <a class="button" href="<?php echo esc_url(wp_nonce_url(admin_url('admin-post.php?action=nwtc_export_contacts'), self::NONCE_ACTION, 'nwtc_nonce')); ?>"><?php esc_html_e('Export CSV', 'northwestern-traffic-crm'); ?></a>
            </header>

            <section class="nwtc-columns">
                <article class="nwtc-panel">
                    <h2><?php esc_html_e('Add or update contact', 'northwestern-traffic-crm'); ?></h2>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" class="nwtc-form">
                        <input type="hidden" name="action" value="nwtc_save_contact">
                        <?php wp_nonce_field(self::NONCE_ACTION, 'nwtc_nonce'); ?>
                        <label><?php esc_html_e('Name', 'northwestern-traffic-crm'); ?><input name="name" required></label>
                        <label><?php esc_html_e('Email', 'northwestern-traffic-crm'); ?><input name="email" type="email"></label>
                        <label><?php esc_html_e('Phone', 'northwestern-traffic-crm'); ?><input name="phone"></label>
                        <label><?php esc_html_e('Status', 'northwestern-traffic-crm'); ?>
                            <select name="status">
                                <option value="lead">Lead</option>
                                <option value="quoted">Quoted</option>
                                <option value="client">Client</option>
                                <option value="follow_up">Follow-up</option>
                            </select>
                        </label>
                        <label><?php esc_html_e('Source', 'northwestern-traffic-crm'); ?><input name="source" placeholder="website, referral, phone"></label>
                        <button class="button button-primary" type="submit"><?php esc_html_e('Save Contact', 'northwestern-traffic-crm'); ?></button>
                    </form>
                </article>

                <article class="nwtc-panel">
                    <h2><?php esc_html_e('Log communication', 'northwestern-traffic-crm'); ?></h2>
                    <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" class="nwtc-form">
                        <input type="hidden" name="action" value="nwtc_save_note">
                        <?php wp_nonce_field(self::NONCE_ACTION, 'nwtc_nonce'); ?>
                        <label><?php esc_html_e('Contact', 'northwestern-traffic-crm'); ?>
                            <select name="contact_id" required>
                                <option value=""><?php esc_html_e('Choose contact', 'northwestern-traffic-crm'); ?></option>
                                <?php foreach ($contacts as $contact) : ?>
                                    <option value="<?php echo esc_attr((string) $contact->id); ?>" <?php selected($selected, (int) $contact->id); ?>>
                                        <?php echo esc_html($contact->name); ?>
                                    </option>
                                <?php endforeach; ?>
                            </select>
                        </label>
                        <label><?php esc_html_e('Type', 'northwestern-traffic-crm'); ?>
                            <select name="note_type">
                                <option value="note">Note</option>
                                <option value="call">Call</option>
                                <option value="email">Email</option>
                                <option value="text">Text</option>
                                <option value="task">Task</option>
                            </select>
                        </label>
                        <label><?php esc_html_e('Details', 'northwestern-traffic-crm'); ?><textarea name="body" rows="5" required></textarea></label>
                        <button class="button button-primary" type="submit"><?php esc_html_e('Save Communication', 'northwestern-traffic-crm'); ?></button>
                    </form>
                </article>
            </section>

            <section class="nwtc-panel">
                <h2><?php esc_html_e('Contact list', 'northwestern-traffic-crm'); ?></h2>
                <?php $this->render_contacts_table($contacts, true); ?>
            </section>

            <?php if ($selected) : ?>
                <section class="nwtc-panel">
                    <h2><?php esc_html_e('Communication timeline', 'northwestern-traffic-crm'); ?></h2>
                    <?php if ($notes) : ?>
                        <ol class="nwtc-timeline">
                            <?php foreach ($notes as $note) : ?>
                                <li>
                                    <strong><?php echo esc_html(ucfirst($note->note_type)); ?></strong>
                                    <time><?php echo esc_html(mysql2date(get_option('date_format') . ' ' . get_option('time_format'), $note->created_at)); ?></time>
                                    <p><?php echo esc_html($note->body); ?></p>
                                </li>
                            <?php endforeach; ?>
                        </ol>
                    <?php else : ?>
                        <p><?php esc_html_e('No communication logged yet.', 'northwestern-traffic-crm'); ?></p>
                    <?php endif; ?>
                </section>
            <?php endif; ?>
        </div>
        <?php
    }

    public function render_links(): void {
        $this->require_admin();
        global $wpdb;
        $links = $wpdb->get_results("SELECT l.*, COUNT(c.id) AS clicks FROM {$this->table('links')} l LEFT JOIN {$this->table('clicks')} c ON c.link_id = l.id GROUP BY l.id ORDER BY l.updated_at DESC");
        ?>
        <div class="wrap nwtc-wrap">
            <header class="nwtc-header">
                <div>
                    <p><?php esc_html_e('Trackable shortcuts', 'northwestern-traffic-crm'); ?></p>
                    <h1><?php esc_html_e('Easy Links', 'northwestern-traffic-crm'); ?></h1>
                </div>
            </header>

            <section class="nwtc-panel">
                <h2><?php esc_html_e('Create link', 'northwestern-traffic-crm'); ?></h2>
                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" class="nwtc-form nwtc-inline-form">
                    <input type="hidden" name="action" value="nwtc_save_link">
                    <?php wp_nonce_field(self::NONCE_ACTION, 'nwtc_nonce'); ?>
                    <label><?php esc_html_e('Key', 'northwestern-traffic-crm'); ?><input name="link_key" placeholder="quote-request" required></label>
                    <label><?php esc_html_e('Label', 'northwestern-traffic-crm'); ?><input name="label" placeholder="Get a quote" required></label>
                    <label><?php esc_html_e('Destination URL', 'northwestern-traffic-crm'); ?><input name="destination_url" type="url" placeholder="https://example.com/quote" required></label>
                    <label><?php esc_html_e('Campaign', 'northwestern-traffic-crm'); ?><input name="campaign" placeholder="spring-mailer"></label>
                    <button class="button button-primary" type="submit"><?php esc_html_e('Save Link', 'northwestern-traffic-crm'); ?></button>
                </form>
            </section>

            <section class="nwtc-panel">
                <h2><?php esc_html_e('Saved links', 'northwestern-traffic-crm'); ?></h2>
                <table class="widefat striped">
                    <thead><tr><th>Label</th><th>Shortcode</th><th>Tracked URL</th><th>Clicks</th><th></th></tr></thead>
                    <tbody>
                    <?php foreach ($links as $link) : ?>
                        <tr>
                            <td><?php echo esc_html($link->label); ?><br><small><?php echo esc_html($link->campaign); ?></small></td>
                            <td><code>[nw_easy_link key="<?php echo esc_attr($link->link_key); ?>"]</code></td>
                            <td><input readonly value="<?php echo esc_attr(add_query_arg('nwlink', rawurlencode($link->link_key), home_url('/'))); ?>" class="nwtc-copy-input"></td>
                            <td><?php echo esc_html((string) $link->clicks); ?></td>
                            <td>
                                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                                    <input type="hidden" name="action" value="nwtc_delete_link">
                                    <input type="hidden" name="link_id" value="<?php echo esc_attr((string) $link->id); ?>">
                                    <?php wp_nonce_field(self::NONCE_ACTION, 'nwtc_nonce'); ?>
                                    <button class="button-link-delete" type="submit"><?php esc_html_e('Disable', 'northwestern-traffic-crm'); ?></button>
                                </form>
                            </td>
                        </tr>
                    <?php endforeach; ?>
                    </tbody>
                </table>
            </section>
        </div>
        <?php
    }

    public function render_settings(): void {
        $this->require_admin();
        $settings = $this->settings();
        ?>
        <div class="wrap nwtc-wrap">
            <header class="nwtc-header">
                <div>
                    <p><?php esc_html_e('Temporary operating console', 'northwestern-traffic-crm'); ?></p>
                    <h1><?php esc_html_e('Settings', 'northwestern-traffic-crm'); ?></h1>
                </div>
            </header>
            <section class="nwtc-panel">
                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" class="nwtc-form">
                    <input type="hidden" name="action" value="nwtc_save_settings">
                    <?php wp_nonce_field(self::NONCE_ACTION, 'nwtc_nonce'); ?>
                    <label><?php esc_html_e('Group name', 'northwestern-traffic-crm'); ?><input name="group_name" value="<?php echo esc_attr($settings['group_name']); ?>"></label>
                    <label><?php esc_html_e('Lead notification email', 'northwestern-traffic-crm'); ?><input name="lead_email" type="email" value="<?php echo esc_attr($settings['lead_email']); ?>"></label>
                    <label><?php esc_html_e('Traffic retention days', 'northwestern-traffic-crm'); ?><input name="retention_days" type="number" min="30" max="730" value="<?php echo esc_attr((string) $settings['retention_days']); ?>"></label>
                    <label class="nwtc-check"><input name="crm_sync_enabled" type="checkbox" value="1" <?php checked((int) $settings['crm_sync_enabled'], 1); ?>> <?php esc_html_e('Sync leads and Easy Links to CRM API', 'northwestern-traffic-crm'); ?></label>
                    <label class="nwtc-check"><input name="crm_sync_traffic" type="checkbox" value="1" <?php checked((int) $settings['crm_sync_traffic'], 1); ?>> <?php esc_html_e('Sync page visit events to CRM API', 'northwestern-traffic-crm'); ?></label>
                    <label><?php esc_html_e('CRM base URL', 'northwestern-traffic-crm'); ?><input name="crm_base_url" type="url" placeholder="https://crm.example.com" value="<?php echo esc_attr($settings['crm_base_url']); ?>"></label>
                    <label><?php esc_html_e('CRM API key', 'northwestern-traffic-crm'); ?><input name="crm_api_key" type="password" autocomplete="new-password" value="<?php echo esc_attr($settings['crm_api_key']); ?>"></label>
                    <p><code>[nw_lead_form source="homepage"]</code></p>
                    <button class="button button-primary" type="submit"><?php esc_html_e('Save Settings', 'northwestern-traffic-crm'); ?></button>
                </form>
            </section>
        </div>
        <?php
    }

    public function save_settings(): void {
        $this->verify_admin_post();
        update_option(self::OPTION_SETTINGS, [
            'group_name' => sanitize_text_field(wp_unslash($_POST['group_name'] ?? 'Northwestern Insurance Group')),
            'lead_email' => sanitize_email(wp_unslash($_POST['lead_email'] ?? get_option('admin_email'))),
            'retention_days' => max(30, min(730, absint($_POST['retention_days'] ?? 180))),
            'crm_sync_enabled' => isset($_POST['crm_sync_enabled']) ? 1 : 0,
            'crm_sync_traffic' => isset($_POST['crm_sync_traffic']) ? 1 : 0,
            'crm_base_url' => esc_url_raw(wp_unslash($_POST['crm_base_url'] ?? '')),
            'crm_api_key' => sanitize_text_field(wp_unslash($_POST['crm_api_key'] ?? '')),
        ]);
        wp_safe_redirect(admin_url('admin.php?page=nwtc-settings&updated=1'));
        exit;
    }

    public function save_link(): void {
        $this->verify_admin_post();
        global $wpdb;

        $key = sanitize_key(wp_unslash($_POST['link_key'] ?? ''));
        $url = esc_url_raw(wp_unslash($_POST['destination_url'] ?? ''));
        if (!$key || !$url) {
            wp_safe_redirect(admin_url('admin.php?page=nwtc-links&error=1'));
            exit;
        }

        $now = current_time('mysql');
        $data = [
            'link_key' => $key,
            'label' => sanitize_text_field(wp_unslash($_POST['label'] ?? $key)),
            'destination_url' => $url,
            'campaign' => sanitize_text_field(wp_unslash($_POST['campaign'] ?? '')),
            'active' => 1,
            'updated_at' => $now,
        ];

        $exists = $wpdb->get_var($wpdb->prepare("SELECT id FROM {$this->table('links')} WHERE link_key = %s", $key));
        if ($exists) {
            $wpdb->update($this->table('links'), $data, ['id' => (int) $exists]);
        } else {
            $data['created_at'] = $now;
            $wpdb->insert($this->table('links'), $data);
        }

        wp_safe_redirect(admin_url('admin.php?page=nwtc-links&updated=1'));
        exit;
    }

    public function delete_link(): void {
        $this->verify_admin_post();
        global $wpdb;
        $wpdb->update($this->table('links'), ['active' => 0, 'updated_at' => current_time('mysql')], ['id' => absint($_POST['link_id'] ?? 0)]);
        wp_safe_redirect(admin_url('admin.php?page=nwtc-links&updated=1'));
        exit;
    }

    public function export_contacts_csv(): void {
        $this->require_admin();
        if (!isset($_GET['nwtc_nonce']) || !wp_verify_nonce(sanitize_text_field(wp_unslash($_GET['nwtc_nonce'])), self::NONCE_ACTION)) {
            wp_die(esc_html__('Invalid request.', 'northwestern-traffic-crm'), 403);
        }

        global $wpdb;
        $contacts = $wpdb->get_results("SELECT name, email, phone, status, source, last_touch, created_at FROM {$this->table('contacts')} ORDER BY COALESCE(last_touch, updated_at) DESC", ARRAY_A);

        nocache_headers();
        header('Content-Type: text/csv; charset=utf-8');
        header('Content-Disposition: attachment; filename=northwestern-traffic-crm-contacts-' . gmdate('Y-m-d') . '.csv');

        $output = fopen('php://output', 'w');
        if ($output) {
            fputcsv($output, ['Name', 'Email', 'Phone', 'Status', 'Source', 'Last Touch', 'Created']);
            foreach ($contacts as $contact) {
                fputcsv($output, $contact);
            }
            fclose($output);
        }
        exit;
    }

    public function save_public_lead(): void {
        if (!isset($_POST['nwtc_nonce']) || !wp_verify_nonce(sanitize_text_field(wp_unslash($_POST['nwtc_nonce'])), 'nwtc_public_lead')) {
            wp_die(esc_html__('Invalid form submission.', 'northwestern-traffic-crm'), 403);
        }

        $contact_id = $this->upsert_contact([
            'name' => sanitize_text_field(wp_unslash($_POST['name'] ?? 'Website Lead')),
            'email' => sanitize_email(wp_unslash($_POST['email'] ?? '')),
            'phone' => sanitize_text_field(wp_unslash($_POST['phone'] ?? '')),
            'status' => 'lead',
            'source' => sanitize_text_field(wp_unslash($_POST['source'] ?? 'website')),
        ]);

        $message = sanitize_textarea_field(wp_unslash($_POST['message'] ?? ''));
        if ($message) {
            $this->insert_note($contact_id, 'website', $message);
        }

        $this->crm_post('/api/wp/lead', [
            'name' => sanitize_text_field(wp_unslash($_POST['name'] ?? 'Website Lead')),
            'email' => sanitize_email(wp_unslash($_POST['email'] ?? '')),
            'phone' => sanitize_text_field(wp_unslash($_POST['phone'] ?? '')),
            'source' => sanitize_text_field(wp_unslash($_POST['source'] ?? 'website')),
            'message' => $message,
            'page_url' => wp_get_referer() ?: home_url('/'),
            'referrer' => isset($_SERVER['HTTP_REFERER']) ? esc_url_raw(wp_unslash($_SERVER['HTTP_REFERER'])) : '',
            'visitor_id_hash' => $this->visitor_id_hash($this->visitor_id()),
            'metadata' => [
                'wordpress_contact_id' => $contact_id,
            ],
        ]);

        $settings = $this->settings();
        if (!empty($settings['lead_email'])) {
            wp_mail(
                $settings['lead_email'],
                sprintf(__('New website lead: %s', 'northwestern-traffic-crm'), sanitize_text_field(wp_unslash($_POST['name'] ?? 'Website Lead'))),
                $message ?: __('A new lead submitted the website form.', 'northwestern-traffic-crm')
            );
        }

        wp_safe_redirect(wp_get_referer() ?: home_url('/'));
        exit;
    }

    public function save_contact(): void {
        $this->verify_admin_post();
        $this->upsert_contact([
            'name' => sanitize_text_field(wp_unslash($_POST['name'] ?? '')),
            'email' => sanitize_email(wp_unslash($_POST['email'] ?? '')),
            'phone' => sanitize_text_field(wp_unslash($_POST['phone'] ?? '')),
            'status' => sanitize_key(wp_unslash($_POST['status'] ?? 'lead')),
            'source' => sanitize_text_field(wp_unslash($_POST['source'] ?? 'manual')),
        ]);
        wp_safe_redirect(admin_url('admin.php?page=nwtc-contacts&updated=1'));
        exit;
    }

    public function save_note(): void {
        $this->verify_admin_post();
        $contact_id = absint($_POST['contact_id'] ?? 0);
        $body = sanitize_textarea_field(wp_unslash($_POST['body'] ?? ''));
        if ($contact_id && $body) {
            $this->insert_note($contact_id, sanitize_key(wp_unslash($_POST['note_type'] ?? 'note')), $body);
        }
        wp_safe_redirect(admin_url('admin.php?page=nwtc-contacts&contact_id=' . $contact_id . '&updated=1'));
        exit;
    }

    private function dashboard_metrics(): array {
        global $wpdb;
        $since = gmdate('Y-m-d H:i:s', strtotime('-30 days', current_time('timestamp', true)));

        return [
            __('Visits, 30 days', 'northwestern-traffic-crm') => (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM {$this->table('visits')} WHERE created_at >= %s", $since)),
            __('Unique visitors', 'northwestern-traffic-crm') => (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(DISTINCT visitor_id) FROM {$this->table('visits')} WHERE created_at >= %s", $since)),
            __('Tracked clicks', 'northwestern-traffic-crm') => (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM {$this->table('clicks')} WHERE created_at >= %s", $since)),
            __('Open contacts', 'northwestern-traffic-crm') => (int) $wpdb->get_var("SELECT COUNT(*) FROM {$this->table('contacts')} WHERE status IN ('lead','quoted','follow_up')"),
        ];
    }

    private function top_pages(): array {
        global $wpdb;
        $since = gmdate('Y-m-d H:i:s', strtotime('-30 days', current_time('timestamp', true)));
        return $wpdb->get_results($wpdb->prepare("SELECT COALESCE(NULLIF(page_title, ''), page_url) AS page_title, COUNT(*) AS views FROM {$this->table('visits')} WHERE created_at >= %s GROUP BY page_title ORDER BY views DESC LIMIT 8", $since), ARRAY_A);
    }

    private function top_sources(): array {
        global $wpdb;
        $since = gmdate('Y-m-d H:i:s', strtotime('-30 days', current_time('timestamp', true)));
        return $wpdb->get_results($wpdb->prepare("SELECT COALESCE(NULLIF(utm_source, ''), 'direct / unknown') AS source, COUNT(*) AS views FROM {$this->table('visits')} WHERE created_at >= %s GROUP BY source ORDER BY views DESC LIMIT 8", $since), ARRAY_A);
    }

    private function top_links(): array {
        global $wpdb;
        $since = gmdate('Y-m-d H:i:s', strtotime('-30 days', current_time('timestamp', true)));
        return $wpdb->get_results($wpdb->prepare(
            "SELECT l.label, COALESCE(NULLIF(l.campaign, ''), 'uncategorized') AS campaign, COUNT(c.id) AS clicks
            FROM {$this->table('links')} l
            LEFT JOIN {$this->table('clicks')} c ON c.link_id = l.id AND c.created_at >= %s
            WHERE l.active = 1
            GROUP BY l.id
            ORDER BY clicks DESC, l.updated_at DESC
            LIMIT 8",
            $since
        ), ARRAY_A);
    }

    private function contact_pipeline(): array {
        global $wpdb;
        $rows = $wpdb->get_results("SELECT status, COUNT(*) AS total FROM {$this->table('contacts')} GROUP BY status ORDER BY total DESC", ARRAY_A);
        $pipeline = [
            'lead' => 0,
            'follow_up' => 0,
            'quoted' => 0,
            'client' => 0,
        ];

        foreach ($rows as $row) {
            $pipeline[(string) $row['status']] = (int) $row['total'];
        }

        return $pipeline;
    }

    private function recent_activity(int $limit = 8): array {
        global $wpdb;
        return $wpdb->get_results($wpdb->prepare(
            "SELECT n.note_type, n.body, n.created_at, c.name
            FROM {$this->table('notes')} n
            INNER JOIN {$this->table('contacts')} c ON c.id = n.contact_id
            ORDER BY n.created_at DESC
            LIMIT %d",
            $limit
        ));
    }

    private function recent_contacts(int $limit): array {
        global $wpdb;
        return $wpdb->get_results($wpdb->prepare("SELECT * FROM {$this->table('contacts')} ORDER BY COALESCE(last_touch, updated_at) DESC LIMIT %d", $limit));
    }

    private function contact_notes(int $contact_id): array {
        global $wpdb;
        return $wpdb->get_results($wpdb->prepare("SELECT * FROM {$this->table('notes')} WHERE contact_id = %d ORDER BY created_at DESC LIMIT 40", $contact_id));
    }

    private function render_table(array $rows, array $columns): void {
        if (!$rows) {
            echo '<p>' . esc_html__('No data yet.', 'northwestern-traffic-crm') . '</p>';
            return;
        }

        echo '<table class="widefat striped"><thead><tr>';
        foreach ($columns as $label) {
            echo '<th>' . esc_html($label) . '</th>';
        }
        echo '</tr></thead><tbody>';
        foreach ($rows as $row) {
            echo '<tr>';
            foreach ($columns as $key => $label) {
                echo '<td>' . esc_html((string) ($row[$key] ?? '')) . '</td>';
            }
            echo '</tr>';
        }
        echo '</tbody></table>';
    }

    private function render_pipeline(array $pipeline): void {
        if (!$pipeline) {
            echo '<p>' . esc_html__('No contacts yet.', 'northwestern-traffic-crm') . '</p>';
            return;
        }

        $labels = [
            'lead' => __('Lead', 'northwestern-traffic-crm'),
            'follow_up' => __('Follow-up', 'northwestern-traffic-crm'),
            'quoted' => __('Quoted', 'northwestern-traffic-crm'),
            'client' => __('Client', 'northwestern-traffic-crm'),
        ];

        echo '<div class="nwtc-pipeline">';
        foreach ($labels as $status => $label) {
            $total = (int) ($pipeline[$status] ?? 0);
            echo '<div class="nwtc-pipeline-item"><span>' . esc_html($label) . '</span><strong>' . esc_html((string) $total) . '</strong></div>';
        }
        echo '</div>';
    }

    private function render_activity(array $activity): void {
        if (!$activity) {
            echo '<p>' . esc_html__('No communication logged yet.', 'northwestern-traffic-crm') . '</p>';
            return;
        }
        ?>
        <ol class="nwtc-timeline">
            <?php foreach ($activity as $item) : ?>
                <li>
                    <strong><?php echo esc_html($item->name . ' · ' . ucfirst($item->note_type)); ?></strong>
                    <time><?php echo esc_html(mysql2date(get_option('date_format') . ' ' . get_option('time_format'), $item->created_at)); ?></time>
                    <p><?php echo esc_html(wp_trim_words($item->body, 24)); ?></p>
                </li>
            <?php endforeach; ?>
        </ol>
        <?php
    }

    private function render_contacts_table(array $contacts, bool $with_actions = false): void {
        if (!$contacts) {
            echo '<p>' . esc_html__('No contacts yet.', 'northwestern-traffic-crm') . '</p>';
            return;
        }
        ?>
        <table class="widefat striped">
            <thead><tr><th>Name</th><th>Email</th><th>Phone</th><th>Status</th><th>Source</th><?php if ($with_actions) : ?><th></th><?php endif; ?></tr></thead>
            <tbody>
            <?php foreach ($contacts as $contact) : ?>
                <tr>
                    <td><?php echo esc_html($contact->name); ?></td>
                    <td><?php echo esc_html($contact->email); ?></td>
                    <td><?php echo esc_html($contact->phone); ?></td>
                    <td><span class="nwtc-status"><?php echo esc_html($contact->status); ?></span></td>
                    <td><?php echo esc_html($contact->source); ?></td>
                    <?php if ($with_actions) : ?>
                        <td><a href="<?php echo esc_url(admin_url('admin.php?page=nwtc-contacts&contact_id=' . (int) $contact->id)); ?>"><?php esc_html_e('Timeline', 'northwestern-traffic-crm'); ?></a></td>
                    <?php endif; ?>
                </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
        <?php
    }

    private function upsert_contact(array $data): int {
        global $wpdb;

        $name = trim((string) ($data['name'] ?? ''));
        if (!$name) {
            $name = 'Website Lead';
        }

        $email = (string) ($data['email'] ?? '');
        $phone = (string) ($data['phone'] ?? '');
        $now = current_time('mysql');
        $existing = null;

        if ($email) {
            $existing = $wpdb->get_var($wpdb->prepare("SELECT id FROM {$this->table('contacts')} WHERE email = %s", $email));
        }
        if (!$existing && $phone) {
            $existing = $wpdb->get_var($wpdb->prepare("SELECT id FROM {$this->table('contacts')} WHERE phone = %s", $phone));
        }

        $row = [
            'name' => $name,
            'email' => $email,
            'phone' => $phone,
            'status' => sanitize_key($data['status'] ?? 'lead') ?: 'lead',
            'source' => sanitize_text_field($data['source'] ?? 'manual'),
            'last_touch' => $now,
            'updated_at' => $now,
        ];

        if ($existing) {
            $wpdb->update($this->table('contacts'), $row, ['id' => (int) $existing]);
            return (int) $existing;
        }

        $row['created_at'] = $now;
        $wpdb->insert($this->table('contacts'), $row);
        return (int) $wpdb->insert_id;
    }

    private function insert_note(int $contact_id, string $type, string $body): void {
        global $wpdb;
        $wpdb->insert($this->table('notes'), [
            'contact_id' => $contact_id,
            'note_type' => $type ?: 'note',
            'body' => $body,
            'created_by' => get_current_user_id() ?: null,
            'created_at' => current_time('mysql'),
        ]);
        $wpdb->update($this->table('contacts'), ['last_touch' => current_time('mysql'), 'updated_at' => current_time('mysql')], ['id' => $contact_id]);
    }

    private function settings(): array {
        $settings = get_option(self::OPTION_SETTINGS, []);
        return wp_parse_args(is_array($settings) ? $settings : [], [
            'group_name' => 'Northwestern Insurance Group',
            'lead_email' => get_option('admin_email'),
            'retention_days' => 180,
            'crm_sync_enabled' => 0,
            'crm_sync_traffic' => 0,
            'crm_base_url' => '',
            'crm_api_key' => '',
        ]);
    }

    private function crm_syncs_traffic(): bool {
        $settings = $this->settings();
        return (bool) ((int) $settings['crm_sync_enabled'] && (int) $settings['crm_sync_traffic']);
    }

    private function crm_post(string $path, array $payload): void {
        $settings = $this->settings();
        if (!(int) $settings['crm_sync_enabled'] || empty($settings['crm_base_url']) || empty($settings['crm_api_key'])) {
            return;
        }

        $url = trailingslashit((string) $settings['crm_base_url']) . ltrim($path, '/');
        wp_remote_post($url, [
            'timeout' => 1.5,
            'blocking' => false,
            'headers' => [
                'Content-Type' => 'application/json',
                'X-API-Key' => (string) $settings['crm_api_key'],
            ],
            'body' => wp_json_encode($payload),
        ]);
    }

    private function maybe_cleanup(): void {
        if (wp_rand(1, 200) !== 1) {
            return;
        }

        global $wpdb;
        $days = max(30, absint($this->settings()['retention_days']));
        $cutoff = gmdate('Y-m-d H:i:s', strtotime("-{$days} days", current_time('timestamp', true)));
        $wpdb->query($wpdb->prepare("DELETE FROM {$this->table('visits')} WHERE created_at < %s", $cutoff));
        $wpdb->query($wpdb->prepare("DELETE FROM {$this->table('clicks')} WHERE created_at < %s", $cutoff));
    }

    private function visitor_id(): string {
        if (!empty($_COOKIE[self::COOKIE])) {
            return sanitize_key(wp_unslash($_COOKIE[self::COOKIE]));
        }

        $visitor_id = wp_generate_uuid4();
        setcookie(self::COOKIE, $visitor_id, [
            'expires' => time() + YEAR_IN_SECONDS,
            'path' => COOKIEPATH ?: '/',
            'domain' => COOKIE_DOMAIN,
            'secure' => is_ssl(),
            'httponly' => true,
            'samesite' => 'Lax',
        ]);

        $_COOKIE[self::COOKIE] = $visitor_id;
        return $visitor_id;
    }

    private function current_url(): string {
        $scheme = is_ssl() ? 'https://' : 'http://';
        $host = isset($_SERVER['HTTP_HOST']) ? sanitize_text_field(wp_unslash($_SERVER['HTTP_HOST'])) : wp_parse_url(home_url(), PHP_URL_HOST);
        $uri = isset($_SERVER['REQUEST_URI']) ? wp_unslash($_SERVER['REQUEST_URI']) : '/';
        return $scheme . $host . esc_url_raw($uri);
    }

    private function is_bot(): bool {
        $agent = strtolower($this->user_agent());
        return (bool) preg_match('/bot|crawl|spider|slurp|preview|facebookexternalhit|monitoring/i', $agent);
    }

    private function user_agent(): string {
        return isset($_SERVER['HTTP_USER_AGENT']) ? sanitize_text_field(wp_unslash($_SERVER['HTTP_USER_AGENT'])) : '';
    }

    private function ip_hash(): string {
        $ip = isset($_SERVER['REMOTE_ADDR']) ? sanitize_text_field(wp_unslash($_SERVER['REMOTE_ADDR'])) : '';
        return $ip ? hash('sha256', wp_salt('auth') . $ip) : '';
    }

    private function visitor_id_hash(string $visitor_id): string {
        return hash('sha256', wp_salt('auth') . $visitor_id);
    }

    private function table(string $name): string {
        global $wpdb;
        return $wpdb->prefix . 'nwtc_' . $name;
    }

    private function require_admin(): void {
        if (!current_user_can('manage_options')) {
            wp_die(esc_html__('You do not have permission to access this page.', 'northwestern-traffic-crm'), 403);
        }
    }

    private function verify_admin_post(): void {
        $this->require_admin();
        if (!isset($_POST['nwtc_nonce']) || !wp_verify_nonce(sanitize_text_field(wp_unslash($_POST['nwtc_nonce'])), self::NONCE_ACTION)) {
            wp_die(esc_html__('Invalid request.', 'northwestern-traffic-crm'), 403);
        }
    }
}

Northwestern_Traffic_CRM::instance();
