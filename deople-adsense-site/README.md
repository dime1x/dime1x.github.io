# Deople — AdSense-ready website starter

A responsive static English knowledge website for `https://deople.ai`.

## Included
- Home page with client-side article search
- 4 topic pages: Technology, Productivity, Education, Lifestyle
- 8 original starter guides
- About, Contact, Privacy Policy, Terms of Use, Disclaimer
- Responsive navigation and layout
- SEO title/description/canonical tags, robots.txt, sitemap.xml
- No third-party scripts, trackers, or ad code by default

## Deploy to GitHub Pages
1. Extract this ZIP.
2. Upload the **contents** of the `deople-adsense-site` folder to the root of your GitHub Pages repository.
3. In repository Settings → Pages, select the correct branch and root folder.
4. Ensure the custom domain is set to `deople.ai` and DNS is configured correctly.
5. Open `https://deople.ai/`, then test every page and mobile layout.
6. Submit `https://deople.ai/sitemap.xml` in Google Search Console.

## Before applying to AdSense — important
No website can be guaranteed approval. This starter is a foundation, not a promise of approval. Before applying:
- Replace or expand the starter articles with your own reviewed, genuinely useful content. Eight articles alone may be insufficient.
- Add author/editor information and a transparent editorial process if applicable.
- Verify the contact email `hello@deople.ai` works; replace it if needed.
- Rewrite the privacy policy to accurately disclose your actual host, analytics, cookies, advertising partners, and consent setup.
- Check that all pages work, navigation is clear, and there are no placeholder pages or broken links.
- Make sure you own or have rights to all text, images, and other assets.
- Do not add copied articles, spun AI text, scraped snippets, or doorway pages.
- Read current AdSense program policies and Google publisher policies before applying.

## Adding AdSense later
After approval, insert the official AdSense code supplied in your AdSense account into the `<head>` of pages (or use a shared template/build process). Do not invent a publisher ID or ad unit code. If using auto ads, configure them in AdSense. Add a valid `ads.txt` only with the exact line supplied by Google for your publisher account.

## Contact email
The current sample address is `hello@deople.ai`. Confirm that this mailbox exists and can receive mail before publishing.

## Editing articles
Each article is a standalone HTML file in `articles/`. Keep titles, descriptions, canonical URLs, and sitemap entries synchronized when adding or changing pages.
