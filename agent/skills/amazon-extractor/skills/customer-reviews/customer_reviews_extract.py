"""Extract structured markdown from Amazon customer_reviews HTML chunk."""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from bs4 import Tag

try:
    from .manifest_sync import update_manifest_block_for_output
except ImportError:
    from manifest_sync import update_manifest_block_for_output


@dataclass(frozen=True)
class ReviewItem:
    title: str
    rating: str
    author: str
    review_date: str
    verified_purchase: bool
    helpful_text: str
    body: str


def _normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _clean_text_from_tag(tag: Tag) -> str:
    cloned_soup = BeautifulSoup(str(tag), "lxml")
    cloned_root = cloned_soup.find(True)
    if cloned_root is None:
        return ""

    for br_tag in cloned_root.find_all("br"):
        br_tag.replace_with(" ")
    for script_like in cloned_root.find_all(["script", "style"]):
        script_like.decompose()
    return _normalize_text(cloned_root.get_text(" ", strip=True))


def _extract_review_title(review_node: Tag) -> str:
    title_node = review_node.select_one('[data-hook="review-title"]')
    if title_node is None:
        return ""

    direct_spans = title_node.find_all("span", recursive=False)
    non_empty_spans = [_normalize_text(span.get_text(" ", strip=True)) for span in direct_spans]
    non_empty_spans = [text for text in non_empty_spans if text]
    if non_empty_spans:
        return non_empty_spans[-1]

    title_text = _normalize_text(title_node.get_text(" ", strip=True))
    return re.sub(r"^\d(?:\.\d)?\s+out\s+of\s+5\s+stars\s*", "", title_text, flags=re.IGNORECASE)


def _extract_review_body(review_node: Tag) -> str:
    collapsed = review_node.select_one('[data-hook="review-collapsed"]')
    if collapsed is not None:
        return _clean_text_from_tag(collapsed)

    review_body = review_node.select_one('[data-hook="review-body"]')
    if review_body is not None:
        return _clean_text_from_tag(review_body)

    return ""


def _extract_reviews(soup: BeautifulSoup) -> list[ReviewItem]:
    review_nodes = soup.select("li.review.aok-relative")
    if not review_nodes:
        review_nodes = soup.select("li.review_aok_relative")

    reviews: list[ReviewItem] = []
    for review_node in review_nodes:
        rating_node = review_node.select_one(
            '[data-hook="review-star-rating"], [data-hook="cmps-review-star-rating"]'
        )
        author_node = review_node.select_one(".a-profile-name")
        date_node = review_node.select_one('[data-hook="review-date"]')
        verified_node = review_node.select_one('[data-hook="avp-badge"]')
        helpful_node = review_node.select_one('[data-hook="helpful-vote-statement"]')

        reviews.append(
            ReviewItem(
                title=_extract_review_title(review_node),
                rating=_normalize_text(rating_node.get_text(" ", strip=True)) if rating_node else "",
                author=_normalize_text(author_node.get_text(" ", strip=True)) if author_node else "",
                review_date=_normalize_text(date_node.get_text(" ", strip=True)) if date_node else "",
                verified_purchase=verified_node is not None,
                helpful_text=_normalize_text(helpful_node.get_text(" ", strip=True)) if helpful_node else "",
                body=_extract_review_body(review_node),
            )
        )

    return reviews


def _extract_histogram_lines(soup: BeautifulSoup) -> list[str]:
    lines: list[str] = []
    for anchor in soup.select("#histogramTable a"):
        aria_label = _normalize_text(anchor.get("aria-label", ""))
        if aria_label:
            lines.append(aria_label)
    return lines


def _render_markdown(soup: BeautifulSoup, reviews: list[ReviewItem]) -> str:
    rating_text = _normalize_text(
        soup.select_one('[data-hook="rating-out-of-text"]').get_text(" ", strip=True)
    ) if soup.select_one('[data-hook="rating-out-of-text"]') else ""
    total_text = _normalize_text(
        soup.select_one('[data-hook="total-review-count"]').get_text(" ", strip=True)
    ) if soup.select_one('[data-hook="total-review-count"]') else ""

    lines: list[str] = ["# Customer Reviews", ""]
    lines.extend(["## Summary", ""])

    if rating_text:
        lines.append(f"- Overall rating: {rating_text}")
    if total_text:
        lines.append(f"- Total ratings: {total_text}")

    histogram_lines = _extract_histogram_lines(soup)
    if histogram_lines:
        lines.append("- Rating distribution:")
        for item in histogram_lines:
            lines.append(f"  - {item}")

    lines.extend(["", f"## Review Items ({len(reviews)})", ""])
    for index, review in enumerate(reviews, start=1):
        heading = review.title if review.title else f"Review {index}"
        lines.append(f"### {index}. {heading}")
        if review.rating:
            lines.append(f"- Rating: {review.rating}")
        if review.author:
            lines.append(f"- Author: {review.author}")
        if review.review_date:
            lines.append(f"- Date: {review.review_date}")
        lines.append(f"- Verified purchase: {'Yes' if review.verified_purchase else 'No'}")
        if review.helpful_text:
            lines.append(f"- Helpful: {review.helpful_text}")
        lines.append("")
        lines.append(review.body if review.body else "(No review body extracted)")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def extract_customer_reviews_markdown(html_path: Path, out_path: Path | None = None) -> Path:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
    reviews = _extract_reviews(soup)
    markdown = _render_markdown(soup, reviews)

    output_path = out_path if out_path else html_path.with_name("customer_reviews_extracted.md")
    output_path.write_text(markdown, encoding="utf-8")
    update_manifest_block_for_output(
        html_path=html_path,
        block_name="customer_reviews_extracted",
        output_path=output_path,
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract customer_reviews.html into markdown")
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--out", type=Path, default=None, help="Output markdown path")
    args = parser.parse_args()

    output_path = extract_customer_reviews_markdown(args.html_file, args.out)
    print(output_path)


if __name__ == "__main__":
    main()
