document.addEventListener('DOMContentLoaded', () => {
	document.querySelectorAll('[data-dashboard-chart]').forEach((figure) => {
		const canvas = figure.querySelector('canvas');
		const status = figure.querySelector('[role="status"]');
		const retry = figure.querySelector('[data-chart-retry]');
		const legend = figure.querySelector('[data-chart-legend]');
		const totalDisplay = figure.querySelector('[data-donut-total]');
		const doughnut = figure.dataset.chartType === 'doughnut';
		const lineChart = figure.dataset.chartType === 'line';
		const vertical = lineChart || figure.dataset.chartType === 'vertical-bar';
		const unit = figure.dataset.unit || 'order';
		const money = Boolean(figure.dataset.currency);
		const moneyFormat = new Intl.NumberFormat('en-GB', {style: 'currency', currency: figure.dataset.currency || 'GBP'});
		const percentFormat = new Intl.NumberFormat('en-GB', {maximumFractionDigits: 1});
		let chart = null;
		let loading = false;
		const loadChart = async () => {
			if (loading) return;
			loading = true;
			retry.hidden = true;
			if (legend) legend.hidden = true;
			if (totalDisplay) totalDisplay.hidden = true;
			status.hidden = false;
			status.textContent = 'Loading chart...';
			const controller = new AbortController();
			const timeout = setTimeout(() => controller.abort(), 12000);
			try {
				if (typeof Chart !== 'function') throw new Error('Chart library unavailable');
				const response = await fetch(figure.dataset.source, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}, signal: controller.signal});
				if (!response.ok || response.redirected || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Chart request failed');
				const data = await response.json();
				if (chart) chart.destroy();
				canvas.hidden = data.empty;
				status.hidden = !data.empty;
				status.textContent = data.empty ? figure.dataset.empty : '';
				if (data.empty) {
					figure.dataset.chartState = 'empty';
					return;
				}
				const counts = data.datasets[0].data;
				if (lineChart) {
					data.datasets.forEach((dataset) => {
						dataset.borderWidth = 2;
						dataset.borderColor = dataset.backgroundColor;
						dataset.pointRadius = 2;
						dataset.tension = 0.15;
					});
				}
				const total = counts.reduce((sum, count) => sum + count, 0);
				const percentage = (index) => percentFormat.format(data.percentages?.[index] ?? (total ? counts[index] * 100 / total : 0));
				if (doughnut) {
					data.datasets[0].borderWidth = 2;
					data.datasets[0].borderColor = '#fafbf8';
					if (totalDisplay) {
						totalDisplay.querySelector('strong').textContent = total;
						totalDisplay.hidden = false;
					}
					if (legend) {
						legend.replaceChildren();
						data.labels.forEach((label, index) => {
							const item = document.createElement('li');
							const swatch = document.createElement('span');
							swatch.className = 'operations-legend-swatch';
							swatch.style.backgroundColor = data.datasets[0].backgroundColor[index];
							const text = document.createElement('span');
							text.textContent = `${label}: ${percentage(index)}% (${counts[index]} ${unit}${counts[index] === 1 ? '' : 's'})`;
							item.append(swatch, text);
							legend.append(item);
						});
						legend.hidden = false;
					}
				}
				chart = new Chart(canvas, {
					type: doughnut ? 'doughnut' : lineChart ? 'line' : 'bar', data,
					options: {
						indexAxis: doughnut || vertical ? 'x' : 'y', responsive: true, maintainAspectRatio: false, animation: {duration: 350}, layout: {padding: 8},
						cutout: doughnut ? '68%' : undefined,
						plugins: {legend: {display: !doughnut && data.datasets.length > 1, position: 'bottom', labels: {boxWidth: 10, font: {family: 'Montserrat', size: 10}}}, tooltip: {displayColors: true, callbacks: doughnut ? {label: (context) => `${context.raw} ${unit}${context.raw === 1 ? '' : 's'} (${percentage(context.dataIndex)}%)`} : money ? {label: (context) => `${context.dataset.label}: ${moneyFormat.format(context.raw)}`} : {}}},
						scales: doughnut ? {} : {
							x: vertical ? {grid: {display: false}, ticks: {font: {family: 'Montserrat', size: 10}, maxRotation: 45}} : {beginAtZero: true, suggestedMax: 1, grid: {color: 'rgba(23,63,53,0.09)'}, border: {display: false}, ticks: {precision: money ? 2 : 0, callback: money ? (value) => moneyFormat.format(value) : undefined, font: {family: 'Montserrat', size: 11}, color: '#657168'}},
							y: vertical ? {beginAtZero: true, grid: {color: 'rgba(23,63,53,0.09)'}, ticks: {precision: money ? 2 : 0, callback: money ? (value) => moneyFormat.format(value) : undefined, font: {family: 'Montserrat', size: 10}}} : {grid: {display: false}, border: {display: false}, ticks: {font: {family: 'Montserrat', size: 11}, color: '#223b31', callback: function(value) {const label = this.getLabelForValue(value); return label.length > 32 ? [label.slice(0, 32), label.slice(32, 64) + (label.length > 64 ? '...' : '')] : label;}}},
						},
					},
				});
				figure.dataset.chartState = 'ready';
			} catch (error) {
				if (legend) legend.hidden = true;
				if (totalDisplay) totalDisplay.hidden = true;
				canvas.hidden = true;
				status.hidden = false;
				status.textContent = 'Chart unavailable. Please try again.';
				retry.hidden = false;
				figure.dataset.chartState = 'error';
			} finally {
				clearTimeout(timeout);
				loading = false;
			}
		};
		retry.addEventListener('click', loadChart);
		loadChart();
	});
});