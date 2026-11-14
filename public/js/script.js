(function () {
    "use strict";

    function initializeWebsite() {

        // Mobile Navigation
        const menuBtn = document.getElementById("menuBtn");
        const navLinks = document.querySelector(".nav-links");

        if (menuBtn && navLinks) {
            menuBtn.addEventListener("click", function () {
                navLinks.classList.toggle("active");
            });
        }

        // Ward Elements
        const wardSelect = document.getElementById("wardSelect");
        const wardMessage = document.getElementById("wardMessage");

        async function loadWards() {
            if (!wardSelect) {
                console.error("wardSelect element not found");
                return;
            }

            wardSelect.innerHTML =
                '<option value="">Loading Vijayawada wards...</option>';

            try {
                const response = await fetch("/api/wards", {
                    method: "GET",
                    cache: "no-store"
                });

                if (!response.ok) {
                    throw new Error("API Error: " + response.status);
                }

                const data = await response.json();

                if (!data.success || !Array.isArray(data.wards)) {
                    throw new Error("Invalid API response");
                }

                wardSelect.innerHTML =
                    '<option value="">Select Ward / Area</option>';

                data.wards.forEach(function (ward) {
                    const option = document.createElement("option");

                    option.value = ward.ward_number;

                    const area = ward.area_name &&
                        ward.area_name !== "Area details pending"
                        ? ward.area_name
                        : "Area details pending";

                    option.textContent =
                        "Ward " + ward.ward_number + " — " + area;

                    wardSelect.appendChild(option);
                });

                if (wardMessage) {
                    wardMessage.textContent =
                        data.total_wards + " Vijayawada wards loaded.";
                }

                console.log("Ward data loaded:", data.total_wards);

            } catch (error) {
                console.error("Ward API Error:", error);

                wardSelect.innerHTML =
                    '<option value="">Unable to load wards</option>';

                if (wardMessage) {
                    wardMessage.textContent =
                        "Unable to load ward details. Please refresh.";
                }
            }
        }

        loadWards();

        // Ward Selection
        window.selectWard = function () {
            if (!wardSelect) return;

            const selected = wardSelect.options[
                wardSelect.selectedIndex
            ];

            if (wardMessage) {
                wardMessage.textContent = wardSelect.value
                    ? "Selected: " + selected.textContent
                    : "";
            }
        };

        // Category Selection
        window.showCategory = function (category) {
            const results = document.getElementById("results");
            if (!results) return;

            results.innerHTML = "";

            const card = document.createElement("div");
            card.className = "result-card";

            const heading = document.createElement("h3");
            heading.textContent = category;

            const info = document.createElement("p");
            info.textContent = wardSelect && wardSelect.value
                ? "Selected Ward: " + wardSelect.value
                : "Please select your Ward / Area first.";

            const message = document.createElement("p");
            message.textContent =
                "Verified shop and price data will appear here when available.";

            card.append(heading, info, message);
            results.appendChild(card);

            const offers = document.getElementById("offers");
            if (offers) {
                offers.scrollIntoView({ behavior: "smooth" });
            }
        };

        // Search
        window.searchItems = function () {
            const searchInput =
                document.getElementById("searchInput");

            const results = document.getElementById("results");

            if (!searchInput || !results) return;

            const query = searchInput.value.trim();

            results.innerHTML = "";

            const card = document.createElement("div");
            card.className = "result-card";

            if (!query) {
                card.textContent =
                    "Please enter a product, shop or category name.";
            } else {
                const heading = document.createElement("h3");
                heading.textContent = "Search: " + query;

                const message = document.createElement("p");
                message.textContent =
                    "No verified matching shop or product data is available yet.";

                card.append(heading, message);
            }

            results.appendChild(card);
        };

        const searchInput =
            document.getElementById("searchInput");

        if (searchInput) {
            searchInput.addEventListener("keydown", function (event) {
                if (event.key === "Enter") {
                    window.searchItems();
                }
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializeWebsite);
    } else {
        initializeWebsite();
    }

})();
