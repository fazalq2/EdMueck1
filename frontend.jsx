import React, { useState, useRef, useEffect } from "react";

export default function PartsFinder() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState(0);
  const resultsRef = useRef(null);

  const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:5000";

  const performSearch = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError("");
    setStep(0);
    setResult(null);

    try {
      // Step 1: Initial query
      setStep(1);
      await new Promise((r) => setTimeout(r, 800));

      // Step 2: AI interpretation (external database lookup)
      setStep(2);
      await new Promise((r) => setTimeout(r, 1200));

      // Step 3: Internal matching
      setStep(3);

      const response = await fetch(`${API_BASE}/api/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });

      if (!response.ok) throw new Error("Search failed");
      const data = await response.json();

      // Step 4: Display results
      setStep(4);
      setResult(data);

      setTimeout(() => {
        resultsRef.current?.scrollIntoView({ behavior: "smooth" });
      }, 500);
    } catch (err) {
      setError(err.message || "An error occurred");
      setStep(0);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-green-900 via-green-800 to-emerald-900">
      {/* Header */}
      <div className="bg-black/30 backdrop-blur-sm border-b border-green-700/50">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <h1 className="text-4xl font-bold text-white mb-2">AgriParts Pro</h1>
          <p className="text-green-200">Find it. Get it. Fast.</p>
        </div>
      </div>

      {/* Hero Section */}
      <div className="relative overflow-hidden py-20">
        {/* Background image overlay */}
        <div
          className="absolute inset-0 opacity-20"
          style={{
            backgroundImage:
              'url("data:image/svg+xml,%3Csvg width="60" height="60" viewBox="0 0 60 60" xmlns="http://www.w3.org/2000/svg"%3E%3Cg fill="none" fill-rule="evenodd"%3E%3Cg fill="%23ffffff" fill-opacity="0.1"%3E%3Cpath d="M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z"/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")',
          }}
        ></div>

        <div className="max-w-4xl mx-auto px-6 relative z-10">
          {/* Search Form */}
          <form onSubmit={performSearch} className="mb-12">
            <div className="relative">
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="What can I help you find today? (e.g., 'Glow plug for Branson 2100 tractor')"
                className="w-full px-8 py-5 rounded-lg bg-white/95 backdrop-blur border-2 border-green-400 text-lg focus:outline-none focus:ring-2 focus:ring-yellow-400 focus:border-transparent transition-all placeholder-gray-500"
                disabled={loading}
              />
              <button
                type="submit"
                disabled={loading}
                className="absolute right-2 top-1/2 -translate-y-1/2 bg-black hover:bg-gray-900 text-white px-6 py-2 rounded font-bold transition-all disabled:opacity-50"
              >
                {loading ? "..." : "Search"}
              </button>
            </div>
          </form>

          {/* Process Visualization */}
          {loading && (
            <div className="space-y-4">
              <ProcessStep
                number={1}
                title="Receiving your query"
                active={step >= 1}
              />
              <ProcessStep
                number={2}
                title="Consulting AI database"
                active={step >= 2}
              />
              <ProcessStep
                number={3}
                title="Matching internal inventory"
                active={step >= 3}
              />
              <ProcessStep
                number={4}
                title="Preparing results"
                active={step >= 4}
              />
            </div>
          )}

          {error && (
            <div className="bg-red-500/20 border border-red-500 text-red-100 px-6 py-4 rounded-lg">
              {error}
            </div>
          )}
        </div>
      </div>

      {/* Results Section */}
      {result && (
        <div ref={resultsRef} className="max-w-4xl mx-auto px-6 py-16">
          {result.results.length > 0 ? (
            <ProductCard
              product={result.results[0]}
              interpretation={result.interpretation}
            />
          ) : (
            <div className="bg-white/10 backdrop-blur border border-white/20 rounded-lg p-8 text-center">
              <h3 className="text-2xl font-bold text-white mb-4">
                No matches found
              </h3>
              <p className="text-gray-200">
                We couldn't find a match for "{result.query}". Please try:
              </p>
              <ul className="mt-4 text-gray-300 text-sm space-y-2">
                <li>• Being more specific with the equipment model</li>
                <li>• Using alternative part names</li>
                <li>• Checking the SKU if you have it</li>
              </ul>
            </div>
          )}

          {/* Alternative Matches */}
          {result.all_matches.length > 1 && (
            <div className="mt-12">
              <h3 className="text-2xl font-bold text-white mb-6">
                Other possible matches:
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {result.all_matches.slice(1, 5).map((product, idx) => (
                  <ProductCardCompact key={idx} product={product} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ProcessStep({ number, title, active }) {
  return (
    <div
      className={`flex items-center gap-4 p-4 rounded-lg transition-all ${
        active
          ? "bg-yellow-400/30 border border-yellow-400"
          : "bg-white/5 border border-white/10"
      }`}
    >
      <div
        className={`w-10 h-10 rounded-full flex items-center justify-center font-bold text-lg ${
          active ? "bg-yellow-400 text-black" : "bg-white/20 text-white"
        }`}
      >
        {number}
      </div>
      <div>
        <p className="text-white font-semibold">{title}</p>
        {active && <p className="text-yellow-200 text-sm">Processing...</p>}
      </div>
    </div>
  );
}

function ProductCard({ product, interpretation }) {
  return (
    <div className="bg-white/10 backdrop-blur border border-white/20 rounded-xl overflow-hidden hover:border-yellow-400/50 transition-all">
      {/* Header */}
      <div className="bg-gradient-to-r from-yellow-600/40 to-orange-600/40 border-b border-white/10 p-8">
        <div className="flex justify-between items-start gap-6">
          <div className="flex-1">
            <h2 className="text-3xl font-bold text-white mb-2">
              {product.title}
            </h2>
            <p className="text-lg text-yellow-300 font-semibold">
              SKU: {product.sku}
            </p>
          </div>
          <div className="text-right">
            <div className="text-4xl font-bold text-yellow-300">
              ${product.price.toFixed(2)}
            </div>
            <div
              className={`text-sm font-semibold mt-2 px-3 py-1 rounded ${
                product.inventory > 0
                  ? "bg-green-500/30 text-green-200"
                  : "bg-red-500/30 text-red-200"
              }`}
            >
              {product.inventory > 0
                ? `${product.inventory} in stock`
                : "Out of stock"}
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="p-8 space-y-8">
        {/* How we found this */}
        <div className="bg-blue-500/10 border border-blue-400/30 rounded-lg p-6">
          <h3 className="text-blue-200 font-semibold mb-2 flex items-center gap-2">
            <span className="text-lg">ℹ️</span> How we found this
          </h3>
          <div className="space-y-2 text-blue-100 text-sm">
            {interpretation.confidence && (
              <p>
                • <strong>Interpretation confidence:</strong>{" "}
                {interpretation.confidence}
              </p>
            )}
            {interpretation.equipment && (
              <p>
                • <strong>Equipment detected:</strong>{" "}
                {interpretation.equipment}
              </p>
            )}
            {interpretation.part_type && (
              <p>
                • <strong>Part type:</strong> {interpretation.part_type}
              </p>
            )}
          </div>
        </div>

        {/* Specifications */}
        <div>
          <h3 className="text-xl font-bold text-white mb-4">Specifications</h3>
          <div className="grid grid-cols-2 gap-6 text-white">
            <div>
              <p className="text-gray-300 text-sm">Weight</p>
              <p className="text-lg font-semibold">
                {product.weight} {product.weight_unit}
              </p>
            </div>
            <div>
              <p className="text-gray-300 text-sm">Type</p>
              <p className="text-lg font-semibold">{product.type}</p>
            </div>
          </div>
        </div>

        {/* Compatible Equipment */}
        {product.tags && product.tags.length > 0 && (
          <div>
            <h3 className="text-xl font-bold text-white mb-4">
              Compatible Equipment
            </h3>
            <div className="flex flex-wrap gap-3">
              {product.tags.slice(0, 10).map((tag, idx) => (
                <span
                  key={idx}
                  className="bg-green-500/30 border border-green-400/50 text-green-100 px-3 py-1 rounded-full text-sm"
                >
                  {tag.trim()}
                </span>
              ))}
              {product.tags.length > 10 && (
                <span className="text-gray-300 text-sm px-2 py-1">
                  +{product.tags.length - 10} more
                </span>
              )}
            </div>
          </div>
        )}

        {/* Description */}
        {product.description && product.description !== "" && (
          <div>
            <h3 className="text-xl font-bold text-white mb-2">Description</h3>
            <div
              className="text-gray-200 prose prose-invert max-w-none"
              dangerouslySetInnerHTML={{ __html: product.description }}
            />
          </div>
        )}

        {/* Disclaimer */}
        <div className="bg-orange-500/10 border border-orange-400/50 rounded-lg p-6 mt-8">
          <h4 className="font-bold text-orange-200 mb-3 flex items-center gap-2">
            <span className="text-lg">⚠️</span> Disclaimer
          </h4>
          <p className="text-orange-100/80 text-sm leading-relaxed">
            Based on available information, the system has identified this as
            the most likely match for your query. However, we recommend
            verifying compatibility with your specific equipment before
            purchase. Parts may be renamed or rebranded. When in doubt, please
            contact our support team or check the equipment manual.
          </p>
        </div>

        {/* Action Button */}
        <button className="w-full bg-gradient-to-r from-yellow-500 to-orange-500 hover:from-yellow-600 hover:to-orange-600 text-black font-bold py-4 rounded-lg transition-all transform hover:scale-105">
          Add to Cart
        </button>
      </div>
    </div>
  );
}

function ProductCardCompact({ product }) {
  return (
    <div className="bg-white/5 border border-white/10 rounded-lg p-6 hover:border-yellow-400/50 transition-all cursor-pointer group">
      <h4 className="font-bold text-white mb-2 group-hover:text-yellow-300 transition-colors">
        {product.title}
      </h4>
      <p className="text-yellow-300 font-semibold text-sm mb-2">
        SKU: {product.sku}
      </p>
      <div className="flex justify-between items-end">
        <span className="text-2xl font-bold text-white">
          ${product.price.toFixed(2)}
        </span>
        <span
          className={`text-xs font-semibold px-2 py-1 rounded ${
            product.inventory > 0
              ? "bg-green-500/30 text-green-200"
              : "bg-red-500/30 text-red-200"
          }`}
        >
          {product.inventory > 0 ? "In stock" : "Out of stock"}
        </span>
      </div>
    </div>
  );
}
