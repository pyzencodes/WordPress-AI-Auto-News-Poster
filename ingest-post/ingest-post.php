<?php
/*
Plugin Name: Ingest Post API (Token)
Description: Token kontrollü içerik ekleme + kategori (varsa bulur/yoksa oluşturur) + medya sideload. Varsayılan: yorumlar kapalı, öne çıkan görseli içerik başına da ekler.
Version: 1.3
Author: WP-Haber Bot
*/

if (!defined('ABSPATH')) exit;

// İstersen wp-config.php içinde define('INGEST_SECRET','...') tanımla; yoksa aşağıdaki yedek kullanılacak.
if (!defined('INGEST_SECRET')) {
    define('INGEST_SECRET', 'n:TWVE+b***');
}

add_action('rest_api_init', function () {
    register_rest_route('ingest/v1', '/post', [
        'methods'  => 'POST',
        'callback' => 'ingest_create_post',
        'permission_callback' => '__return_true',
    ]);
});

function ingest_get_token_from_request( WP_REST_Request $request ) {
    $hdr = $request->get_header('X-INGEST-TOKEN');
    if (!empty($hdr)) return $hdr;
    $qs = $request->get_param('token');
    return $qs ?: '';
}

/** Kategori ID yoksa adıyla bul/oluştur, ID döndür */
function ingest_ensure_category_id($category_id, $category_name) {
    $category_id = intval($category_id ?: 0);
    if ($category_id > 0) {
        return $category_id;
    }
    $name = trim((string)$category_name);
    if ($name === '') return 0;

    $term = term_exists($name, 'category');
    if ($term && !is_wp_error($term)) {
        return intval(is_array($term) ? $term['term_id'] : $term);
    }

    $slug = sanitize_title($name);
    $inserted = wp_insert_term($name, 'category', ['slug' => $slug]);
    if (is_wp_error($inserted)) {
        $inserted = wp_insert_term($name . ' Haberleri', 'category');
    }
    if (is_wp_error($inserted)) {
        return 0;
    }
    return intval($inserted['term_id']);
}

function ingest_create_post( WP_REST_Request $request ) {
    // Token kontrol
    $token = ingest_get_token_from_request($request);
    if ($token !== INGEST_SECRET) {
        return new WP_REST_Response(['error' => 'unauthorized'], 401);
    }

    $title        = sanitize_text_field( $request->get_param('title') ?? '' );
    $content      = wp_kses_post( $request->get_param('content') ?? '' );
    $status       = $request->get_param('status') ?: 'publish';
    $category_id  = $request->get_param('category_id');
    $category_nm  = sanitize_text_field( $request->get_param('category_name') ?? '' );
    $image_url    = esc_url_raw( $request->get_param('image_url') ?? '' );
    $comments     = strtolower( (string)($request->get_param('comments') ?? 'closed') ); // closed|open
    $insert_img   = strtolower( (string)($request->get_param('insert_image_in_content') ?? 'true') ) === 'true';

    if (empty($title) || empty($content)) {
        return new WP_REST_Response(['error' => 'missing_fields'], 400);
    }

    // Kategori
    $cat_id = ingest_ensure_category_id($category_id, $category_nm);

    // Yazı
    $postarr = [
        'post_title'     => $title,
        'post_content'   => $content,
        'post_status'    => $status,
        'post_type'      => 'post',
        'comment_status' => ($comments === 'open' ? 'open' : 'closed'),
        'ping_status'    => 'closed',
    ];
    if ($cat_id > 0) {
        $postarr['post_category'] = [ $cat_id ];
    }

    $post_id = wp_insert_post($postarr, true);
    if (is_wp_error($post_id)) {
        return new WP_REST_Response(['error' => $post_id->get_error_message()], 500);
    }

    // Görsel işle
    if (!empty($image_url)) {
        require_once ABSPATH . 'wp-admin/includes/media.php';
        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/image.php';

        $tmp = download_url( $image_url );
        if (!is_wp_error($tmp)) {
            $file_array = [
                'name'     => basename( parse_url($image_url, PHP_URL_PATH) ),
                'tmp_name' => $tmp,
            ];
            $att_id = media_handle_sideload( $file_array, $post_id, $title );
            if (!is_wp_error($att_id)) {
                set_post_thumbnail( $post_id, $att_id );

                if ($insert_img) {
                    $src = wp_get_attachment_image_src($att_id, 'large');
                    if ($src && !empty($src[0])) {
                        $img_html = '<figure class="ingest-featured"><img src="' . esc_url($src[0]) . '" alt="' . esc_attr($title) . '"></figure>' . "\n";
                        $current = get_post_field('post_content', $post_id);
                        wp_update_post([
                            'ID' => $post_id,
                            'post_content' => $img_html . $current
                        ]);
                    }
                }
            } else {
                @unlink($tmp);
            }
        }
    }

    return new WP_REST_Response([
        'id'        => $post_id,
        'link'      => get_permalink($post_id),
        'category'  => $cat_id,
        'comments'  => get_post_field('comment_status', $post_id),
    ], 200);
}