document.addEventListener('DOMContentLoaded', () => {
	const menuButton = document.querySelector('.menu-toggle');
	const navigation = document.querySelector('.site-header nav');
	if (menuButton && navigation) {
		menuButton.addEventListener('click', () => {
			const isOpen = navigation.classList.toggle('open');
			menuButton.setAttribute('aria-expanded', String(isOpen));
			menuButton.textContent = isOpen ? '✕' : '☰';
		});
	}

	document.querySelectorAll('video[data-playback-rate]').forEach((video) => {
		const rate = parseFloat(video.dataset.playbackRate) || 1;
		const applyRate = () => { video.playbackRate = rate; };
		video.addEventListener('loadedmetadata', applyRate);
		video.addEventListener('play', applyRate);
		applyRate();
	});

	const sameAsShipping = document.querySelector('#id_same_as_shipping');
	const billingFields = document.querySelectorAll('.billing-fields');
	const updateBillingFields = () => {
		billingFields.forEach((field) => { field.hidden = sameAsShipping && sameAsShipping.checked; });
	};
	if (sameAsShipping) {
		sameAsShipping.addEventListener('change', updateBillingFields);
		updateBillingFields();
	}

	const deliverySelect = document.querySelector('#id_shipping_method');
	const deliveryRatesElement = document.querySelector('#delivery-rates');
	const subtotalElement = document.querySelector('#checkout-subtotal');
	const deliveryCostElement = document.querySelector('#checkout-delivery-cost');
	const totalElement = document.querySelector('#checkout-total');
	if (deliverySelect && deliveryRatesElement && subtotalElement && deliveryCostElement && totalElement) {
		const rates = JSON.parse(deliveryRatesElement.textContent);
		const subtotal = Number(subtotalElement.dataset.subtotal || 0);
		const updateDeliveryTotal = () => {
			const selected = rates.find((rate) => rate.code === deliverySelect.value);
			if (!selected) return;
			const freeOver = selected.free_over === null ? null : Number(selected.free_over);
			const delivery = freeOver !== null && subtotal >= freeOver ? 0 : Number(selected.price);
			deliveryCostElement.textContent = delivery === 0 ? 'Free' : `£${delivery.toFixed(2)}`;
			totalElement.textContent = `£${(subtotal + delivery).toFixed(2)}`;
		};
		deliverySelect.addEventListener('change', updateDeliveryTotal);
		updateDeliveryTotal();
	}

	const analyticsTable = document.querySelector('[data-analytics-table]');
	if (analyticsTable) {
		const getRows = () => [...analyticsTable.querySelectorAll('tbody tr[data-filterable]')];
		const filterInput = document.querySelector('[data-analytics-filter]');
		if (filterInput) {
			filterInput.addEventListener('input', () => {
				const query = filterInput.value.trim().toLowerCase();
				getRows().forEach((row) => {
					const haystack = ['type', 'product', 'path', 'target', 'customer'].map((key) => (row.dataset[key] || '').toLowerCase()).join(' ');
					row.hidden = query.length > 0 && !haystack.includes(query);
				});
			});
		}
		analyticsTable.querySelectorAll('th[data-sort-key]').forEach((header) => {
			header.addEventListener('click', () => {
				const key = header.dataset.sortKey;
				const direction = header.dataset.sortDir === 'asc' ? 'desc' : 'asc';
				analyticsTable.querySelectorAll('th[data-sort-key]').forEach((other) => {
					other.dataset.sortDir = '';
					other.classList.remove('is-sorted-asc', 'is-sorted-desc');
				});
				header.dataset.sortDir = direction;
				header.classList.add(direction === 'asc' ? 'is-sorted-asc' : 'is-sorted-desc');
				const tbody = analyticsTable.querySelector('tbody');
				const sorted = getRows().sort((a, b) => {
					const aValue = (a.dataset[key] || '').toLowerCase();
					const bValue = (b.dataset[key] || '').toLowerCase();
					if (aValue < bValue) return direction === 'asc' ? -1 : 1;
					if (aValue > bValue) return direction === 'asc' ? 1 : -1;
					return 0;
				});
				sorted.forEach((row) => tbody.appendChild(row));
			});
		});
	}

	document.querySelectorAll('.message').forEach((message) => {
		window.setTimeout(() => {
			message.classList.add('is-hidden');
			window.setTimeout(() => message.remove(), 400);		}, 2500);
	});

	document.addEventListener('click', (event) => {
		const target = event.target.closest('a, button');
		if (!target || !window.mishaAnalyticsUrl || target.closest('.control-nav')) return;
		const payload = JSON.stringify({path: window.location.pathname, target: (target.innerText || target.getAttribute('aria-label') || target.href || '').trim().slice(0, 255), product_id: target.dataset.productId || null});
		if (navigator.sendBeacon) navigator.sendBeacon(window.mishaAnalyticsUrl, new Blob([payload], {type: 'application/json'}));
	});

	const orderModal = document.querySelector('.order-modal');
	const orderModalPanel = orderModal && orderModal.querySelector('.order-modal-panel');
	const closeOrderModal = () => {
		if (!orderModal) return;
		orderModal.hidden = true;
		orderModalPanel.innerHTML = '';
		document.body.classList.remove('modal-open');
	};
	document.querySelectorAll('[data-order-modal]').forEach((button) => {
		button.addEventListener('click', (event) => {
			const template = document.getElementById(button.dataset.orderModal);
			if (!template || !orderModalPanel) return;
			orderModalPanel.innerHTML = template.innerHTML;
			orderModal.hidden = false;
			document.body.classList.add('modal-open');
			orderModalPanel.querySelector('.modal-close').addEventListener('click', closeOrderModal);
		});
	});
	const selectAll = document.querySelector('[data-select-all]');
	if (selectAll) {
		selectAll.addEventListener('change', () => {
			document.querySelectorAll('[data-order-checkbox]').forEach((checkbox) => {
				checkbox.checked = selectAll.checked;
			});
		});
	}
	if (orderModal) {
		orderModal.querySelector('.order-modal-backdrop').addEventListener('click', closeOrderModal);
		document.addEventListener('keydown', (event) => {
			if (event.key === 'Escape') closeOrderModal();
		});
	}
	document.addEventListener('click', (event) => {
		const copyButton = event.target.closest('[data-copy-target]');
		if (!copyButton) return;
		const input = document.getElementById(copyButton.dataset.copyTarget);
		if (!input) return;
		navigator.clipboard.writeText(input.value).then(() => {
			const originalText = copyButton.textContent;
			copyButton.textContent = 'Copied';
			window.setTimeout(() => { copyButton.textContent = originalText; }, 1600);
		});
	});

	const productModal = document.querySelector('[data-product-modal]');
	const closeProductModal = () => {
		if (!productModal) return;
		productModal.hidden = true;
		document.body.classList.remove('modal-open');
	};
	const openProductModal = () => {
		if (!productModal) return;
		productModal.hidden = false;
		document.body.classList.add('modal-open');
	};
	document.querySelectorAll('[data-product-modal-open]').forEach((button) => button.addEventListener('click', openProductModal));
	document.querySelectorAll('[data-product-modal-close]').forEach((button) => button.addEventListener('click', closeProductModal));
	if (productModal && productModal.dataset.open === 'true') openProductModal();
	if (productModal) productModal.addEventListener('click', (event) => {
		if (event.target.closest('[data-product-modal-close]')) closeProductModal();
	});

	document.addEventListener('keydown', (event) => {
		if (event.key === 'Escape') closeProductModal();
	});

	document.querySelectorAll('[data-gallery]').forEach((gallery) => {
		const images = [...gallery.querySelectorAll('[data-gallery-image]')];
		const thumbnails = [...gallery.querySelectorAll('[data-gallery-thumbnail]')];
		if (images.length < 2) return;
		let current = 0;
		const showImage = (index) => {
			current = (index + images.length) % images.length;
			images.forEach((image, imageIndex) => { image.hidden = imageIndex !== current; });
			thumbnails.forEach((thumbnail, thumbnailIndex) => { thumbnail.classList.toggle('is-active', thumbnailIndex === current); });
		};
		gallery.querySelector('[data-gallery-previous]').addEventListener('click', () => showImage(current - 1));
		gallery.querySelector('[data-gallery-next]').addEventListener('click', () => showImage(current + 1));
		thumbnails.forEach((thumbnail) => thumbnail.addEventListener('click', () => showImage(Number(thumbnail.dataset.galleryThumbnail))));
		showImage(0);
	});

	document.querySelectorAll('[data-account-tab]').forEach((tab) => {
		tab.addEventListener('click', () => {
			const name = tab.dataset.accountTab;
			document.querySelectorAll('[data-account-tab]').forEach((item) => item.classList.toggle('is-active', item === tab));
			document.querySelectorAll('[data-account-panel]').forEach((panel) => {
				const active = panel.dataset.accountPanel === name;
				panel.hidden = !active;
				panel.classList.toggle('is-active', active);
			});
		});
	});
});
