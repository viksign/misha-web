# Google Search and ecommerce readiness

Preferred public origin: https://mishaislandheritage.com

## Audit and verification: 4 October 2026

- Django 5.2.17; installed applications are Django admin/auth/contenttypes,
  sessions/messages/staticfiles/sitemaps, chartjs and shop.
- Existing Product/Collection models, title blocks, sitemap and robots views were
  retained. There were no existing canonical tags, social metadata, JSON-LD, or
  model-level manually authored SEO fields to migrate.
- Public pages were reachable and DEBUG was already disabled. The principal gaps
  were absent canonicals/structured data, generic product/collection descriptions,
  private pages without noindex, and request-host-dependent sitemap generation.
- All 121 discovered Django tests passed against isolated in-memory SQLite.
  `manage.py check` passed and `makemigrations --check --dry-run` found no changes.
- Live MySQL rendering and Cloudflare HTTP checks verified three sitemap sections
  with nine distinct public URLs; all returned 200 and declared matching canonical
  URLs. Product JSON-LD parsed and actual price/currency/image URLs were verified.
- HTTP redirects to HTTPS, missing trailing slashes redirect consistently, and www
  pages declare the preferred non-www canonical. Private/search noindex headers
  and meta tags were verified publicly.
- Cloudflare still served an older cached version of the exact `/robots.txt` after
  the requested purge. The origin and a cache-busted request serve the new version.
  The older entry allows public indexing and declares the correct sitemap, but
  **purge the exact non-www robots URL again or wait for its edge TTL to expire**.
  Confirm the refreshed response contains `Allow: /` and `Disallow: /admin/`.
- Google ownership verification, Rich Results eligibility, actual Google indexing,
  and Merchant Center approval were not performed or claimed.

### Files changed for SEO

`config/settings.py`, `config/urls.py`, `shop/seo.py`, `shop/sitemaps.py`,
`shop/views.py`, `shop/models.py`, `shop/test_seo.py`, `templates/base.html`,
`templates/shop/product_detail.html`, `templates/shop/_product_card.html`, and
`deploy/SEO.md`. No database migration was required.

## Technical implementation

- Existing Django sitemaps are reused. `/sitemap.xml` is an index of the product,
  collection and static-page sitemaps. Each section paginates at 5,000 URLs.
- Active products and collections are included; public static pages are home,
  jewellery, heritage and contact. Products use their existing modification time.
  Collections have no reliable modification timestamp, so no date is invented.
- Sitemap and canonical URLs are always HTTPS on the preferred public origin,
  independent of the incoming hostname or runtime `SITE_URL`.
- Public pages have canonical, title/description, Open Graph and Twitter metadata.
  Existing authored title and description blocks are preserved.
- Product JSON-LD uses real names, descriptions, SKUs, prices, images and available
  stock. Joining an interest list is not a purchase, so zero-stock products remain
  `OutOfStock`, rather than being represented as purchasable pre-orders.
- Homepage Organization/WebSite markup omits unavailable company facts and ratings.
- Private pages and internal catalogue search/filter variants are noindex. Robots
  excludes private routes but allows public content and assets. Robots is not an
  access-control mechanism or a guarantee that an already indexed URL disappears.
- The standard noindex header on sitemap XML is intentional: Google reads the
  sitemap without indexing the XML itself as a search result.
- `www` remains reachable to preserve existing host-scoped customer sessions and
  carts. Its public pages declare the non-www canonical. A forced host migration
  should be planned separately if desired; HTTP-to-HTTPS and trailing-slash
  redirects are preserved.
- Product image dimensions and loading hints are added without recompression or
  design changes. Missing image dimensions do not prevent a page from rendering.

## Search Console: required manual steps

1. Sign in to https://search.google.com/search-console with the business Google
   account. Check for an existing property before adding another.
2. Add a **Domain property** named `mishaislandheritage.com` (no scheme or path).
3. Copy the exact TXT value Google displays. Its format is
   `google-site-verification=YOUR_GOOGLE_GENERATED_TOKEN`. The token cannot be
   generated or guessed by this application.
4. In the domain's authoritative Cloudflare DNS zone, add a TXT record with
   **Name** `@`, **Content** equal to Google's exact value, and automatic TTL.
   Preserve every existing DNS record. TXT records are DNS-only, not proxied.
5. Return to Search Console and select **Verify** once the record has propagated.
6. Under **Sitemaps**, submit `https://mishaislandheritage.com/sitemap.xml`.
7. Inspect the homepage, `/jewellery/`, `/heritage/`, each important active
   `/collection/<slug>/`, and representative `/jewellery/<slug>/` URLs. Run a
   live URL test and request indexing when the page is eligible.
  Initial candidates are `/`, `/jewellery/`, `/heritage/`,
  `/collection/signature/`, and `/jewellery/sacred-roots-pendant/`.
8. Review Page indexing, Sitemaps, Product snippets and Merchant listings reports.
   Confirm Google-selected canonical URLs and actual indexing separately.

If a URL-prefix property is preferred, use `https://mishaislandheritage.com/`.
The existing optional `GOOGLE_SITE_VERIFICATION` setting supports Google's HTML
meta-tag method. Do not replace unrelated Google Analytics or Ads settings.

For HTML-file verification, the uploaded `static/web/google26bfb82dbebf9ae3.html`
is served unchanged at `/google26bfb82dbebf9ae3.html` on both public hostnames.
Keep the file and its root route after verification succeeds; Google may
recheck ownership. The verification file does not belong in the sitemap.

## Merchant Center: required manual steps

1. Run representative product pages through https://search.google.com/test/rich-results.
2. Create/select the business Merchant Center account, verify/claim the website,
   and provide the real business details. No account or feed was submitted here.
3. Configure actual delivery and return/refund policies. Confirm visible product
   shipping information agrees with the live shipping configuration.
4. Configure the GBP catalogue/feed or the appropriate supported website source.
   Use stable product IDs, canonical URLs, real images, prices and availability.
5. Supply legitimate GTINs/MPNs only where they exist; do not invent identifiers,
   ratings, addresses, phone numbers or product certifications.
6. Monitor Merchant Center diagnostics and keep feed availability/prices in sync
   with the storefront and stock system.

## Remaining recommendations

- Active empty collections may be considered thin content. Add useful, accurate
  collection information or disable them when they are not ready for visitors.
- Review existing product/marketing copy and material claims against actual SKUs;
  the SEO work does not rewrite authored claims or rename existing product slugs.
- Review standalone privacy, terms and returns-policy pages before Merchant Center
  submission. A cookie-choice banner is not a substitute for those policies.
- Keep Cloudflare public-page access open to verified search crawlers. A successful
  local HTTP test does not establish that Google has crawled or indexed the site.
- The current five product images were readable and below 2 MiB; no image quality
  reduction was necessary. Reassess image sizes as the catalogue grows.

Expected entry points:

- https://mishaislandheritage.com/robots.txt
- https://mishaislandheritage.com/sitemap.xml