document.addEventListener("DOMContentLoaded", function () {

    // Mobile Navigation Menu
    const menuBtn = document.getElementById("menuBtn");
    const navLinks = document.querySelector(".nav-links");

    if (menuBtn && navLinks) {
        menuBtn.addEventListener("click", function () {
            navLinks.classList.toggle("active");
        });

        navLinks.querySelectorAll("a").forEach(link => {
            link.addEventListener("click", function () {
                navLinks.classList.remove("active");
            });
        });
    }

    // Ward API Integration
    const wardSelect = document.getElementById("wardSelect");
    const wardMessage = document.getElementById("wardMessage");

    async function loadWards() {
        if (!wardSelect) return;

        wardSelect.innerHTML =
            '<option value="">Loading Vijayawada wards...</option>';

        try {
            const response = await fetch("/api/wards");

            if (!response.ok) {
                throw new Error("Ward API request failed");
            }

            const data = await response.json();

            if (!data.success || !Array.isArray(data.wards)) {
                throw new Error("Invalid ward data");
            }

            wardSelect.innerHTML =
                '<option value="">Select Ward / Area</option>';

            data.wards.forEach(ward => {
                const option = document.createElement("option");

                option.value = ward.ward_number;

                const area = ward.area_name || "Area name pending";

                option.textContent =
                    "Ward " + ward.ward_number + " — " + area;

                wardSelect.appendChild(option);
            });

            if (wardMessage) {
                wardMessage.textContent =
                    data.total_wards + " ward records loaded successfully.";
            }

        } catch (error) {
            console.error("Ward loading error:", error);

            wardSelect.innerHTML =
                '<option value="">Unable to load wards. Refresh page.</option>';

            if (wardMessage) {
                wardMessage.textContent =
                    "Ward details could not be loaded. Please try again.";
            }
        }
    }

    loadWards();

    // Ward Selection
    window.selectWard = function () {
        if (!wardSelect || !wardMessage) return;

        const selectedOption =
            wardSelect.options[wardSelect.selectedIndex];

        if (!wardSelect.value) {
            wardMessage.textContent = "";
            return;
        }

        wardMessage.textContent =
            "Selected: " + selectedOption.textContent;
    };

    // Category Selection
    window.showCategory = function (category) {
        const results = document.getElementById("results");

        if (!results) return;

        const selectedWard = wardSelect
            ? wardSelect.value
            : "";

        results.innerHTML = "";

        const card = document.createElement("div");
        card.className = "result-card";

        const heading = document.createElement("h3");
        heading.textContent = category;

        const wardInfo = document.createElement("p");
        wardInfo.textContent = selectedWard
            ? "Selected Ward: " + selectedWard
            : "Please select your Ward / Area.";

        const message = document.createElement("p");
        message.textContent =
            "Verified shop and product listings will appear here once they are added to the database.";

        card.append(heading, wardInfo, message);
        results.appendChild(card);

        document.getElementById("offers").scrollIntoView({
            behavior: "smooth"
        });
    };

    // Search Functionality
    window.searchItems = function () {
        const searchInput =
            document.getElementById("searchInput");

        const results =
            document.getElementById("results");

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
                "Product and shop database search is not connected yet. No unverified results are displayed.";

            card.append(heading, message);
        }

        results.appendChild(card);

        document.getElementById("offers").scrollIntoView({
            behavior: "smooth"
        });
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

});
