"""Chunker package for Amazon product HTML chunking & extraction pipeline.

Reads MCP scraper output (products/{ASIN}/product.html + rankings.jsonl),
chunks into 4 blocks (ppd/customer_reviews/product_details/aplus),
and extracts structured markdown via BS4 + lxml.
"""
