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

    // Search Functionality
    const searchInput = document.getElementById("searchInput");
    const searchBtn = document.getElementById("searchBtn");
    const categoryCards = document.querySelectorAll(".category-card");
    const results = document.getElementById("results");

    function searchCategories() {
        if (!searchInput || !results) return;

        const query = searchInput.value.trim().toLowerCase();

        if (!query) {
            results.innerHTML =
                '<p class="empty-message">Please enter a shop or category name.</p>';
            return;
        }

        let matches = [];

        categoryCards.forEach(card => {
            const text = card.innerText.toLowerCase();

            if (text.includes(query)) {
                matches.push(card);
            }
        });

        if (matches.length > 0) {
            results.innerHTML = "";

            matches.forEach(card => {
                const copy = card.cloneNode(true);
                results.appendChild(copy);
            });

        } else {
            results.innerHTML =
                '<p class="empty-message">No matching categories found. Try another search.</p>';
        }

        document.getElementById("offers").scrollIntoView({
            behavior: "smooth"
        });
    }

    if (searchBtn) {
        searchBtn.addEventListener("click", searchCategories);
    }

    if (searchInput) {
        searchInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                searchCategories();
            }
        });
    }

});
